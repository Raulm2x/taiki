"""Almacén de memorias: creación, lectura, listado y búsqueda."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any, Iterable

from .. import db
from ..domain import ValidationError
from ..util import check_enum, fts_match, now
from . import core
from . import ledger as ledger_lib
from .curate import _set_status
from .lifecycle import (
    approve_memory,
    archive_memory,
    feedback_memory,
    patch_memory,
    pin_memory,
    reject_memory,
    restore_memory,
    supersede_memory,
)
from .models import (
    MEMORY_KINDS,
    MEMORY_SCOPES,
    MEMORY_SOURCES,
    MEMORY_STATUSES,
    PLAYBOOK_DESCRIPTION_CHARS,
    PLAYBOOK_KIND,
    default_key,
    normalize_fingerprint,
    validate_playbook_key,
)

__all__ = [
    "add_memory",
    "approve_memory",
    "archive_memory",
    "feedback_memory",
    "get_memory",
    "list_memories",
    "patch_memory",
    "pin_memory",
    "reject_memory",
    "restore_memory",
    "search_memories",
    "supersede_memory",
]


def add_memory(
    conn: sqlite3.Connection,
    *,
    title: str,
    content: str,
    kind: str = "fact",
    key: str | None = None,
    scope: str = "project",
    description: str = "",
    paths: Iterable[object] | None = None,
    kinds: Iterable[object] | None = None,
    ticket_kinds: Iterable[object] | None = None,
    confidence: float = 0.5,
    source: str = "agent",
    actor: str | None = None,
    session_id: str | None = None,
    ticket_id: str | None = None,
    evidence: Iterable[Any] | None = None,
    files: Iterable[Any] | None = None,
    config: dict[str, Any] | None = None,
    status: str | None = None,
    pinned: bool = False,
    locked: bool = False,
    reason: str | None = None,
) -> dict[str, Any]:
    """Crea una memoria aplicando seguridad, política, dedupe y presupuesto."""
    cfg = core.config(config)
    if not cfg["enabled"]:
        raise ValidationError("La memoria está desactivada en la configuración")
    check_enum(scope, MEMORY_SCOPES, "scope")
    check_enum(kind, MEMORY_KINDS, "kind")
    check_enum(source, MEMORY_SOURCES, "source")
    if status is not None:
        check_enum(status, MEMORY_STATUSES, "status")
        if source not in ("derived", "import"):
            raise ValidationError(
                "El estado explícito solo se admite para source=derived o import; "
                "usa la política balanced o las transiciones del store."
            )
        if status == "superseded":
            raise ValidationError(
                "No se puede crear una memoria directamente superseded; "
                "usa supersede_memory o una clave vigente."
            )

    title_text = core.clean_text(title, "título", int(cfg["max_entry_chars"]))
    description_limit = (
        PLAYBOOK_DESCRIPTION_CHARS
        if kind == PLAYBOOK_KIND
        else int(cfg["max_entry_chars"])
    )
    content_limit = (
        int(cfg["max_playbook_chars"])
        if kind == PLAYBOOK_KIND
        else int(cfg["max_entry_chars"])
    )
    description_text = core.clean_text(
        description,
        "descripción",
        description_limit,
        required=False,
    )
    content_text = core.clean_text(content, "contenido", content_limit)
    level = core.confidence(confidence)
    key_text = core.clean_key(str(key or "").strip() or default_key(title_text))
    if kind == PLAYBOOK_KIND:
        key_text = validate_playbook_key(key_text)
    actor = (
        core.clean_short(actor, "actor", max_chars=200)
        if actor is not None
        else None
    )
    session_id = (
        core.clean_short(session_id, "session_id", max_chars=200)
        if session_id is not None
        else None
    )
    project_paths = core.clean_project_paths(paths)
    kind_list = core.clean_kinds(kinds)
    ticket_kind_list = core.clean_ticket_kinds(ticket_kinds)
    evidence_rows = core.clean_evidence(evidence)
    file_rows = core.clean_files(files, cfg)
    ticket = core.ticket_id(ticket_id)
    for item in evidence_rows:
        if item["kind"] == "ticket" and ticket is None:
            ticket = item["ref"].upper()

    if source == "agent" and not evidence_rows:
        raise ValidationError(
            "Una memoria de origen agent exige al menos una evidencia "
            "(ticket, commit, file, url o session); sin ella no se escribe."
        )

    fingerprint = normalize_fingerprint(title_text, content_text)
    validated = core.validated_evidence(conn, evidence_rows)
    final_status = status or core.decide_status(
        kind=kind,
        source=source,
        evidence_count=len(evidence_rows),
        confidence_value=level,
        text=" ".join((title_text, description_text, content_text, key_text)),
        policy=str(cfg["write_policy"]),
        validated_evidence=len(validated),
        auto_approve=bool(cfg["auto_approve_evidence"]),
    )

    with db.tx(conn):
        duplicate = conn.execute(
            "SELECT * FROM memories WHERE scope = ? AND fingerprint = ? "
            "AND status IN ('active', 'proposed', 'stale') LIMIT 1",
            (scope, fingerprint),
        ).fetchone()
        if duplicate is not None:
            raise ValidationError(
                f"Memoria duplicada: ya existe {duplicate['code']} con el mismo "
                "contenido en este scope."
            )
        previous = conn.execute(
            'SELECT * FROM memories WHERE scope = ? AND "key" = ? '
            "AND status IN ('active', 'proposed', 'stale') "
            "ORDER BY id DESC LIMIT 1",
            (scope, key_text),
        ).fetchone()

        code = core.allocate_code(conn)
        timestamp = now()
        cursor = conn.execute(
            "INSERT INTO memories("
            "code, scope, kind, \"key\", title, description, content, paths, kinds, "
            "ticket_kinds, "
            "status, confidence, source, actor, session_id, ticket_id, "
            "supersedes_id, pinned, locked, created_at, updated_at, archived_at, "
            "fingerprint"
            ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                code,
                scope,
                kind,
                key_text,
                title_text,
                description_text,
                content_text,
                json.dumps(project_paths, ensure_ascii=False)
                if project_paths
                else None,
                json.dumps(kind_list, ensure_ascii=False) if kind_list else None,
                json.dumps(ticket_kind_list, ensure_ascii=False)
                if ticket_kind_list
                else None,
                final_status,
                level,
                source,
                actor,
                session_id,
                ticket,
                int(previous["id"]) if previous is not None else None,
                1 if pinned else 0,
                1 if locked else 0,
                timestamp,
                timestamp,
                timestamp if final_status == "archived" else None,
                fingerprint,
            ),
        )
        memory_id = int(cursor.lastrowid or 0)
        core.insert_evidence(conn, memory_id, evidence_rows)
        core.insert_files(conn, memory_id, file_rows)

        if previous is not None:
            before = ledger_lib.snapshot(conn, int(previous["id"]))
            conn.execute(
                "UPDATE memories SET status = 'superseded', superseded_by = ?, "
                "updated_at = ? WHERE id = ?",
                (code, timestamp, int(previous["id"])),
            )
            after = ledger_lib.snapshot(conn, int(previous["id"]))
            ledger_lib.record(
                conn,
                action="supersede",
                memory_id=int(previous["id"]),
                before=before,
                after=after,
                actor=actor,
                session_id=session_id,
                reason=reason or f"sustituida por {code}",
                meta={"by": code},
            )

        created = ledger_lib.snapshot(conn, memory_id)
        ledger_lib.record(
            conn,
            action="create",
            memory_id=memory_id,
            after=created,
            actor=actor,
            session_id=session_id,
            reason=reason,
        )
        core.check_budget(conn, scope, cfg)
        row = conn.execute(
            "SELECT * FROM memories WHERE id = ?", (memory_id,)
        ).fetchone()
        assert row is not None
        return core.receipt(conn, row, cfg)


def _evidence_root(conn: sqlite3.Connection) -> str | None:
    """Raíz del proyecto a partir del fichero de la DB abierta."""
    try:
        row = conn.execute("PRAGMA database_list").fetchone()
        path = str(row["file"] or "") if row is not None else ""
    except sqlite3.Error:
        return None
    if not path:
        return None
    parent = Path(path).expanduser().parent
    return str(parent.parent if parent.name == ".taiki" else parent)


def _with_freshness(
    conn: sqlite3.Connection,
    data: dict[str, Any],
    mark: bool = True,
) -> dict[str, Any]:
    """Añade la vigencia de la evidencia y, con ``mark``, marca ``stale``."""
    freshness = core.evidence_freshness(
        conn,
        data.get("evidence") or [],
        data.get("files") or [],
        root=_evidence_root(conn),
    )
    data["freshness"] = freshness
    if mark and not freshness["ok"] and data.get("status") == "active":
        reason = "evidencia no vigente: " + "; ".join(freshness["reasons"])
        with db.tx(conn):
            _set_status(conn, data, "stale", action="stale", reason=reason)
        data["status"] = "stale"
    return data


def get_memory(
    conn: sqlite3.Connection,
    code: Any,
    *,
    session_id: str | None = None,
    record_usage: bool = False,
    mark: bool = True,
) -> dict[str, Any]:
    """Devuelve una memoria con evidencia, ficheros y presupuesto.

    ``mark=False`` calcula la vigencia sin mutar el estado: los listados y
    resúmenes no deben degradar memorias como efecto de una lectura.
    """
    row = core.memory_row(conn, code)
    if record_usage:
        with db.tx(conn):
            core.record_usage(conn, int(row["id"]), "get", session_id)
        row = core.memory_row(conn, code)
    return _with_freshness(
        conn, core.row_to_memory(row, conn, details=True), mark=mark
    )


def _ticket_kinds_filter(column: str, values: list[str]) -> tuple[str, list[Any]]:
    """SQL que aplica memorias generales y las etiquetadas con algún tipo."""
    marks = ", ".join("?" for _ in values)
    clause = (
        f"({column} IS NULL OR {column} = '' OR EXISTS ("
        f"SELECT 1 FROM json_each({column}) WHERE value IN ({marks})))"
    )
    return clause, list(values)


def list_memories(
    conn: sqlite3.Connection,
    *,
    scope: str | None = None,
    status: str | None = None,
    kind: str | None = None,
    ticket_kinds: Iterable[object] | None = None,
    key: str | None = None,
    limit: int = 50,
    include_inactive: bool = False,
) -> list[dict[str, Any]]:
    """Lista memorias por scope, estado, tipo, tipo de ticket o clave."""
    if scope is not None:
        check_enum(scope, MEMORY_SCOPES, "scope")
    if status is not None:
        check_enum(status, MEMORY_STATUSES, "status")
    if kind is not None:
        check_enum(kind, MEMORY_KINDS, "kind")
    ticket_kind_list = core.clean_ticket_kinds(ticket_kinds)
    if isinstance(limit, bool) or int(limit) <= 0:
        raise ValidationError("El límite de memorias debe ser mayor que 0")

    where: list[str] = []
    params: list[Any] = []
    if scope:
        where.append("scope = ?")
        params.append(scope)
    if status:
        where.append("status = ?")
        params.append(status)
    elif not include_inactive:
        where.append("status NOT IN ('archived', 'superseded')")
    if kind:
        where.append("kind = ?")
        params.append(kind)
    if ticket_kind_list:
        clause, values = _ticket_kinds_filter("ticket_kinds", ticket_kind_list)
        where.append(clause)
        params.extend(values)
    if key:
        where.append('"key" = ?')
        params.append(str(key).strip())

    sql = "SELECT * FROM memories"
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY pinned DESC, updated_at DESC, id DESC LIMIT ?"
    params.append(int(limit))
    rows = conn.execute(sql, params).fetchall()
    return [core.row_to_memory(row) for row in rows]


def search_memories(
    conn: sqlite3.Connection,
    query: str,
    *,
    scope: str | None = None,
    status: str | None = None,
    ticket_kinds: Iterable[object] | None = None,
    limit: int = 10,
    session_id: str | None = None,
    record_usage: bool = True,
) -> list[dict[str, Any]]:
    """Busca por FTS5 y registra el uso si se pide."""
    match = fts_match(query)
    if match is None:
        return []
    if scope is not None:
        check_enum(scope, MEMORY_SCOPES, "scope")
    if status is not None:
        check_enum(status, MEMORY_STATUSES, "status")
    ticket_kind_list = core.clean_ticket_kinds(ticket_kinds)
    if isinstance(limit, bool) or int(limit) <= 0:
        raise ValidationError("El límite de búsqueda debe ser mayor que 0")

    where = ["memories_fts MATCH ?"]
    params: list[Any] = [match]
    if scope:
        where.append("m.scope = ?")
        params.append(scope)
    if status:
        where.append("m.status = ?")
        params.append(status)
    else:
        where.append("m.status NOT IN ('archived', 'superseded')")
    if ticket_kind_list:
        clause, values = _ticket_kinds_filter("m.ticket_kinds", ticket_kind_list)
        where.append(clause)
        params.extend(values)
    sql = (
        "SELECT m.* FROM memories_fts "
        "JOIN memories m ON m.id = memories_fts.rowid "
        "WHERE " + " AND ".join(where) + " "
        "ORDER BY bm25(memories_fts) LIMIT ?"
    )
    params.append(int(limit))
    rows = conn.execute(sql, params).fetchall()
    if record_usage and rows:
        with db.tx(conn):
            for row in rows:
                core.record_usage(
                    conn,
                    int(row["id"]),
                    "search",
                    session_id,
                    note=query,
                )
    return [
        _with_freshness(conn, core.row_to_memory(row, conn, details=True), mark=False)
        for row in rows
    ]
