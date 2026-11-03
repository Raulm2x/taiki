"""Importación de snapshots de memoria con estrategias keep/replace/new."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

from .. import db
from ..domain import ValidationError
from ..util import now
from . import core
from . import ledger as ledger_lib
from .config import DEFAULT_MEMORY_CONFIG, validate_memory_config
from .export_document import _validated_document, _validated_memory
from .models import BUDGET_STATUSES, MEMORY_CODE_RE, MEMORY_COUNTER

_IMPORT_STRATEGIES = ("keep", "replace", "new")

_COLUMNS = (
    "code",
    "scope",
    "kind",
    '"key"',
    "title",
    "description",
    "content",
    "paths",
    "kinds",
    "ticket_kinds",
    "status",
    "confidence",
    "source",
    "actor",
    "session_id",
    "ticket_id",
    "supersedes_id",
    "superseded_by",
    "pinned",
    "locked",
    "use_count",
    "helpful_count",
    "unhelpful_count",
    "last_used_at",
    "last_briefed_at",
    "created_at",
    "updated_at",
    "archived_at",
    "fingerprint",
)


def _values(memory: dict[str, Any], code: str) -> tuple[Any, ...]:
    return (
        code,
        memory["scope"],
        memory["kind"],
        memory["key"],
        memory["title"],
        memory["description"],
        memory["content"],
        json.dumps(memory["paths"], ensure_ascii=False) if memory["paths"] else None,
        json.dumps(memory["kinds"], ensure_ascii=False) if memory["kinds"] else None,
        json.dumps(memory["ticket_kinds"], ensure_ascii=False)
        if memory["ticket_kinds"]
        else None,
        memory["status"],
        memory["confidence"],
        memory["source"],
        memory["actor"],
        memory["session_id"],
        memory["ticket_id"],
        None,
        memory["superseded_by"],
        1 if memory["pinned"] else 0,
        1 if memory["locked"] else 0,
        memory["use_count"],
        memory["helpful_count"],
        memory["unhelpful_count"],
        memory["last_used_at"],
        memory["last_briefed_at"],
        memory["created_at"],
        memory["updated_at"],
        memory["archived_at"],
        memory["fingerprint"],
    )


def _insert_memory(
    conn: sqlite3.Connection, memory: dict[str, Any], code: str
) -> int:
    placeholders = ", ".join("?" for _ in _COLUMNS)
    cursor = conn.execute(
        f"INSERT INTO memories({', '.join(_COLUMNS)}) VALUES ({placeholders})",
        _values(memory, code),
    )
    memory_id = int(cursor.lastrowid or 0)
    core.insert_evidence(conn, memory_id, memory["evidence"])
    core.insert_files(conn, memory_id, memory["files"])
    return memory_id


def _update_memory(
    conn: sqlite3.Connection, memory_id: int, memory: dict[str, Any], code: str
) -> None:
    assignments = ", ".join(f"{column} = ?" for column in _COLUMNS)
    conn.execute(
        f"UPDATE memories SET {assignments} WHERE id = ?",
        (*_values(memory, code), memory_id),
    )
    conn.execute("DELETE FROM memory_evidence WHERE memory_id = ?", (memory_id,))
    conn.execute("DELETE FROM memory_files WHERE memory_id = ?", (memory_id,))
    core.insert_evidence(conn, memory_id, memory["evidence"])
    core.insert_files(conn, memory_id, memory["files"])


def _advance_counter(conn: sqlite3.Connection, codes: list[str]) -> None:
    numbers = [
        int(code[2:])
        for code in codes
        if MEMORY_CODE_RE.fullmatch(code) is not None
    ]
    if not numbers:
        return
    current = int(db.meta_get(conn, MEMORY_COUNTER, "1") or "1")
    db.meta_set(conn, MEMORY_COUNTER, str(max(current, max(numbers) + 1)))


def _resolve_relations(
    conn: sqlite3.Connection,
    written: list[tuple[dict[str, Any], str, int]],
    code_map: dict[str, str],
) -> None:
    for memory, _stored_code, memory_id in written:
        supersedes = memory.get("supersedes_code")
        if supersedes:
            target = code_map.get(str(supersedes), str(supersedes))
            row = conn.execute(
                "SELECT id FROM memories WHERE code = ?", (target,)
            ).fetchone()
            conn.execute(
                "UPDATE memories SET supersedes_id = ? WHERE id = ?",
                (int(row["id"]) if row is not None else None, memory_id),
            )
        if memory.get("superseded_by"):
            mapped = code_map.get(
                str(memory["superseded_by"]), str(memory["superseded_by"])
            )
            conn.execute(
                "UPDATE memories SET superseded_by = ? WHERE id = ?",
                (mapped, memory_id),
            )


def _import(
    conn: sqlite3.Connection,
    path: str | Path,
    *,
    strategy: str,
    dry_run: bool,
    action: str,
    exact: bool,
    actor: str | None,
    session_id: str | None,
    reason: str | None,
) -> dict[str, Any]:
    if strategy not in _IMPORT_STRATEGIES:
        raise ValidationError(
            f"Estrategia de importación inválida: {strategy!r} "
            f"(usa {', '.join(_IMPORT_STRATEGIES)})"
        )
    target = Path(path).expanduser()
    document = _validated_document(target)
    cfg = validate_memory_config(DEFAULT_MEMORY_CONFIG)
    memories = [_validated_memory(item, cfg) for item in document["memories"]]
    checksum = str(document["checksum"])
    default_reason = (
        f"importación {checksum}"
        if action == "import"
        else f"rollback de snapshot {checksum}"
    )

    if dry_run:
        conn.execute("SAVEPOINT memory_import_dry_run")
    try:
        with db.tx(conn):
            imported: list[str] = []
            updated: list[str] = []
            skipped: list[str] = []
            conflicts: list[dict[str, Any]] = []
            written: list[tuple[dict[str, Any], str, int]] = []
            code_map: dict[str, str] = {}
            target_codes: set[str] = set()
            for memory in memories:
                incoming = memory["code"]
                by_code = conn.execute(
                    "SELECT * FROM memories WHERE code = ?", (incoming,)
                ).fetchone()
                by_key = conn.execute(
                    'SELECT * FROM memories WHERE scope = ? AND "key" = ? '
                    "ORDER BY id LIMIT 1",
                    (memory["scope"], memory["key"]),
                ).fetchone()
                existing = by_code if by_code is not None else by_key
                conflict: dict[str, Any] = {}
                if existing is not None:
                    kind = "code" if by_code is not None else "key"
                    conflict = {
                        "code": incoming,
                        "with": str(existing["code"]),
                        "kind": kind,
                        "strategy": strategy,
                    }
                if strategy == "keep" and existing is not None:
                    skipped.append(incoming)
                    conflicts.append({**conflict, "resolution": "kept"})
                    target_codes.add(str(existing["code"]))
                    code_map[incoming] = str(existing["code"])
                    continue
                if strategy == "replace" and existing is not None:
                    stored_code = str(existing["code"])
                    before = ledger_lib.snapshot(conn, int(existing["id"]))
                    _update_memory(conn, int(existing["id"]), memory, stored_code)
                    after = ledger_lib.snapshot(conn, int(existing["id"]))
                    ledger_lib.record(
                        conn,
                        action=action,
                        memory_id=int(existing["id"]),
                        before=before,
                        after=after,
                        actor=actor,
                        session_id=session_id,
                        reason=reason or default_reason,
                        meta={"checksum": checksum, "strategy": strategy},
                    )
                    updated.append(stored_code)
                    conflicts.append({**conflict, "resolution": "replaced"})
                    target_codes.add(stored_code)
                    code_map[incoming] = stored_code
                    written.append((memory, stored_code, int(existing["id"])))
                    continue
                stored_code = incoming
                if strategy == "new":
                    stored_code = core.allocate_code(conn)
                    code_map[incoming] = stored_code
                    if conflict:
                        conflicts.append({**conflict, "resolution": "new_code"})
                elif conflict:
                    conflicts.append({**conflict, "resolution": "inserted"})
                memory_id = _insert_memory(conn, memory, stored_code)
                after = ledger_lib.snapshot(conn, memory_id)
                ledger_lib.record(
                    conn,
                    action=action,
                    memory_id=memory_id,
                    after=after,
                    actor=actor,
                    session_id=session_id,
                    reason=reason or default_reason,
                    meta={"checksum": checksum, "strategy": strategy},
                )
                imported.append(stored_code)
                target_codes.add(stored_code)
                written.append((memory, stored_code, memory_id))

            _resolve_relations(conn, written, code_map)
            archived: list[str] = []
            if exact:
                for row in conn.execute(
                    "SELECT * FROM memories WHERE status IN (?, ?, ?) "
                    "ORDER BY code",
                    BUDGET_STATUSES,
                ).fetchall():
                    code = str(row["code"])
                    if code in target_codes:
                        continue
                    before = ledger_lib.snapshot(conn, int(row["id"]))
                    timestamp = now()
                    conn.execute(
                        "UPDATE memories SET status = 'archived', archived_at = ?, "
                        "updated_at = ? WHERE id = ?",
                        (timestamp, timestamp, int(row["id"])),
                    )
                    after = ledger_lib.snapshot(conn, int(row["id"]))
                    ledger_lib.record(
                        conn,
                        action="rollback",
                        memory_id=int(row["id"]),
                        before=before,
                        after=after,
                        actor=actor,
                        session_id=session_id,
                        reason=reason or default_reason,
                        meta={"checksum": checksum},
                    )
                    archived.append(code)
            _advance_counter(
                conn, [stored for _memory, stored, _id in written]
            )
            return {
                "path": str(target),
                "strategy": strategy,
                "dry_run": bool(dry_run),
                "version": int(document["version"]),
                "checksum": checksum,
                "imported": imported,
                "updated": updated,
                "skipped": skipped,
                "archived": archived,
                "conflicts": conflicts,
            }
    finally:
        if dry_run:
            conn.execute("ROLLBACK TO memory_import_dry_run")
            conn.execute("RELEASE memory_import_dry_run")


def import_snapshot(
    conn: sqlite3.Connection,
    path: str | Path,
    strategy: str = "keep",
    dry_run: bool = False,
) -> dict[str, Any]:
    """Importa un snapshot con estrategia keep/replace/new y validación."""
    return _import(
        conn,
        path,
        strategy=strategy,
        dry_run=dry_run,
        action="import",
        exact=False,
        actor=None,
        session_id=None,
        reason=None,
    )
