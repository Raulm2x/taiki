"""Export/import versionable y rollback de la memoria.

El snapshot JSON es la fuente de verdad: incluye memorias, playbooks, ficheros,
evidencia, propuestas y relaciones, con versión y checksum determinista. El
Markdown es una vista humana que embebe el mismo JSON en un comentario, así que
también se puede reimportar sin pérdida. Ni la exportación ni la importación
borran filas: los conflictos y el rollback se resuelven con estados y ledger.
"""

from __future__ import annotations

import base64
import hashlib
import json
import sqlite3
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Any

from .. import db
from ..domain import ValidationError
from ..util import check_enum, now, parse_datetime
from . import core
from . import ledger as ledger_lib
from .config import DEFAULT_MEMORY_CONFIG, validate_memory_config
from .models import (
    BUDGET_STATUSES,
    MEMORY_CODE_RE,
    MEMORY_COUNTER,
    MEMORY_KINDS,
    MEMORY_SCOPES,
    MEMORY_SOURCES,
    MEMORY_STATUSES,
    PLAYBOOK_DESCRIPTION_CHARS,
    PLAYBOOK_KIND,
    normalize_fingerprint,
)
from .security import SECRET_PATTERNS, validate_memory_paths

VERSION = 1
FORMAT = "taiki-memory"
_MARKER = "<!-- taiki-memory-json:"
_IMPORT_STRATEGIES = ("keep", "replace", "new")


def _scrub(value: Any) -> str:
    """Sustituye patrones con forma de secreto antes de exportar."""
    text = str(value if value is not None else "")
    for _, pattern in SECRET_PATTERNS:
        text = pattern.sub("[redactado]", text)
    return text


def _checksum(memories: list[dict[str, Any]]) -> str:
    payload = json.dumps(
        {"version": VERSION, "memories": memories},
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    return "sha256:" + hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _code_map(conn: sqlite3.Connection) -> dict[int, str]:
    return {
        int(row["id"]): str(row["code"])
        for row in conn.execute("SELECT id, code FROM memories")
    }


def _memory_document(
    row: sqlite3.Row,
    codes: dict[int, str],
    evidence: list[dict[str, Any]],
    files: list[dict[str, Any]],
) -> dict[str, Any]:
    data = dict(row)
    supersedes = data.get("supersedes_id")
    return {
        "code": str(data["code"]),
        "scope": str(data["scope"]),
        "kind": str(data["kind"]),
        "key": _scrub(data.get("key")),
        "title": _scrub(data.get("title")),
        "description": _scrub(data.get("description")),
        "content": _scrub(data.get("content")),
        "paths": list(core.load_list(data.get("paths"))),
        "kinds": list(core.load_list(data.get("kinds"))),
        "ticket_kinds": list(core.load_list(data.get("ticket_kinds"))),
        "status": str(data["status"]),
        "confidence": float(data.get("confidence") or 0.0),
        "source": str(data.get("source") or "agent"),
        "actor": None if data.get("actor") is None else _scrub(data.get("actor")),
        "session_id": (
            None if data.get("session_id") is None else _scrub(data.get("session_id"))
        ),
        "ticket_id": (
            None if data.get("ticket_id") is None else _scrub(data.get("ticket_id"))
        ),
        "supersedes_code": (
            codes.get(int(supersedes)) if supersedes is not None else None
        ),
        "superseded_by": (
            None if data.get("superseded_by") is None else _scrub(data.get("superseded_by"))
        ),
        "pinned": bool(data.get("pinned")),
        "locked": bool(data.get("locked")),
        "use_count": int(data.get("use_count") or 0),
        "helpful_count": int(data.get("helpful_count") or 0),
        "unhelpful_count": int(data.get("unhelpful_count") or 0),
        "last_used_at": data.get("last_used_at"),
        "last_briefed_at": data.get("last_briefed_at"),
        "created_at": data.get("created_at"),
        "updated_at": data.get("updated_at"),
        "archived_at": data.get("archived_at"),
        "fingerprint": str(data.get("fingerprint") or ""),
        "evidence": [
            {
                "kind": str(item["kind"]),
                "ref": _scrub(item["ref"]),
                "created_at": item.get("created_at"),
            }
            for item in evidence
        ],
        "files": [
            {
                "path": str(item["path"]),
                "content": _scrub(item.get("content")),
                "sha256": str(item.get("sha256") or ""),
            }
            for item in files
        ],
    }


def _document(conn: sqlite3.Connection) -> dict[str, Any]:
    codes = _code_map(conn)
    memories: list[dict[str, Any]] = []
    for row in conn.execute("SELECT * FROM memories ORDER BY code"):
        memory_id = int(row["id"])
        evidence = [
            dict(item)
            for item in conn.execute(
                "SELECT kind, ref, created_at FROM memory_evidence "
                "WHERE memory_id = ? ORDER BY kind, ref",
                (memory_id,),
            )
        ]
        files = [
            dict(item)
            for item in conn.execute(
                "SELECT path, content, sha256 FROM memory_files "
                "WHERE memory_id = ? ORDER BY path",
                (memory_id,),
            )
        ]
        memories.append(_memory_document(row, codes, evidence, files))
    return {
        "format": FORMAT,
        "version": VERSION,
        "generated_at": now(),
        "checksum": _checksum(memories),
        "memories": memories,
    }


def _json_text(document: dict[str, Any]) -> str:
    return (
        json.dumps(document, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    )


def _markdown(document: dict[str, Any]) -> str:
    lines = [
        "# Taiki memory snapshot",
        "",
        f"- format: {document['format']}",
        f"- version: {document['version']}",
        f"- checksum: {document['checksum']}",
        f"- generated_at: {document['generated_at']}",
        f"- memories: {len(document['memories'])}",
        "",
    ]
    for memory in document["memories"]:
        lines.append(
            f"## {memory['code']} · {memory['title']} "
            f"[{memory['status']}] {memory['scope']}/{memory['kind']}"
        )
        if memory.get("description"):
            lines.extend(["", str(memory["description"]), ""])
        lines.extend(["", "```text", str(memory.get("content") or ""), "```", ""])
        if memory.get("evidence"):
            lines.append("Evidencia:")
            for item in memory["evidence"]:
                lines.append(f"- {item['kind']}:{item['ref']}")
            lines.append("")
        if memory.get("files"):
            lines.append("Ficheros:")
            for item in memory["files"]:
                lines.append(f"- {item['path']} ({item['sha256']})")
                lines.extend(["", "```text", item["content"], "```"])
            lines.append("")
    encoded = base64.b64encode(
        _json_text(document).encode("utf-8")
    ).decode("ascii")
    lines.extend(["", _MARKER, encoded, "-->"])
    return "\n".join(lines).rstrip() + "\n"


def _write_atomic(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    handle, temp_path = tempfile.mkstemp(
        prefix=f".{path.name}-", suffix=".tmp", dir=str(path.parent)
    )
    try:
        with open(handle, "w", encoding="utf-8", closefd=True) as stream:
            stream.write(content)
            stream.flush()
            import os

            os.fsync(stream.fileno())
        Path(temp_path).replace(path)
    except BaseException:
        try:
            Path(temp_path).unlink()
        except OSError:
            pass
        raise


def _format_for(path: Path, value: str | None) -> str:
    if value:
        return value
    if path.suffix.lower() in (".md", ".markdown"):
        return "markdown"
    return "json"


def export_snapshot(
    conn: sqlite3.Connection,
    path: str | Path,
    format: str | None = None,
) -> dict[str, Any]:
    """Exporta un snapshot determinista y devuelve sus metadatos."""
    target = Path(path).expanduser()
    kind = _format_for(target, format)
    if kind not in ("json", "markdown"):
        raise ValidationError(
            f"Formato de exportación inválido: {kind!r} (usa json o markdown)"
        )
    document = _document(conn)
    text = _markdown(document) if kind == "markdown" else _json_text(document)
    _write_atomic(target, text)
    return {
        "path": str(target),
        "format": kind,
        "version": VERSION,
        "checksum": document["checksum"],
        "memories": len(document["memories"]),
        "playbooks": sum(
            1 for item in document["memories"] if item["kind"] == PLAYBOOK_KIND
        ),
        "bytes": len(text.encode("utf-8")),
    }


def _read_document(path: Path) -> dict[str, Any]:
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise ValidationError(f"No se pudo leer el snapshot {path}: {exc}") from exc
    if not text.lstrip().startswith("{") and (
        path.suffix.lower() in (".md", ".markdown") or _MARKER in text
    ):
        if _MARKER not in text:
            raise ValidationError(
                f"El Markdown {path} no incluye el snapshot JSON embebido"
            )
        chunk = text.rsplit(_MARKER, 1)[1]
        encoded = chunk.split("-->", 1)[0].strip()
        try:
            text = base64.b64decode(encoded).decode("utf-8")
        except (ValueError, UnicodeDecodeError) as exc:
            raise ValidationError(
                f"El snapshot embebido en {path} está corrupto: {exc}"
            ) from exc
    try:
        document = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ValidationError(f"JSON de snapshot inválido en {path}: {exc}") from exc
    if not isinstance(document, dict):
        raise ValidationError(f"El snapshot {path} debe ser un objeto JSON")
    return document


def _validated_document(path: Path) -> dict[str, Any]:
    document = _read_document(path)
    if document.get("format") != FORMAT:
        raise ValidationError(
            f"El snapshot {path} no es de Taiki (format={document.get('format')!r})"
        )
    if document.get("version") != VERSION:
        raise ValidationError(
            f"Versión de snapshot no soportada: {document.get('version')!r}"
        )
    memories = document.get("memories")
    if not isinstance(memories, list) or not all(
        isinstance(item, dict) for item in memories
    ):
        raise ValidationError(f"El snapshot {path} no contiene una lista de memorias")
    expected = _checksum(memories)
    if document.get("checksum") != expected:
        raise ValidationError(
            f"Checksum del snapshot inválido en {path}: "
            f"{document.get('checksum')!r} != {expected!r}"
        )
    return document


def _string(value: Any, field: str, *, required: bool = True) -> str:
    text = str(value if value is not None else "").strip()
    if required and not text:
        raise ValidationError(f"El snapshot no incluye {field}")
    return text


def _timestamp(value: Any) -> str:
    parsed = parse_datetime(value)
    if parsed is None:
        return now()
    return parsed.isoformat(timespec="seconds")


def _validated_memory(raw: dict[str, Any], cfg: dict[str, Any]) -> dict[str, Any]:
    code = _string(raw.get("code"), "código M-####")
    if not MEMORY_CODE_RE.fullmatch(code):
        raise ValidationError(f"Código de memoria inválido en snapshot: {code!r}")
    scope = _string(raw.get("scope"), "scope")
    kind = _string(raw.get("kind"), "kind")
    status = _string(raw.get("status"), "status")
    source = _string(raw.get("source"), "source")
    check_enum(scope, MEMORY_SCOPES, "scope")
    check_enum(kind, MEMORY_KINDS, "kind")
    check_enum(status, MEMORY_STATUSES, "status")
    check_enum(source, MEMORY_SOURCES, "source")
    title = core.clean_text(
        _string(raw.get("title"), "título"), "título", int(cfg["max_entry_chars"])
    )
    description_limit = (
        PLAYBOOK_DESCRIPTION_CHARS
        if kind == PLAYBOOK_KIND
        else int(cfg["max_entry_chars"])
    )
    description = core.clean_text(
        raw.get("description") or "",
        "descripción",
        description_limit,
        required=False,
    )
    content_limit = (
        int(cfg["max_playbook_chars"])
        if kind == PLAYBOOK_KIND
        else int(cfg["max_entry_chars"])
    )
    content = core.clean_text(
        _string(raw.get("content"), "contenido"), "contenido", content_limit
    )
    key = core.clean_key(
        _string(raw.get("key"), "clave") or normalize_fingerprint(title)[:80]
    )
    if kind == PLAYBOOK_KIND and not key.startswith("playbook:"):
        raise ValidationError(
            f"El playbook {code} necesita una clave playbook:<slug> en el snapshot"
        )
    paths = validate_memory_paths(raw.get("paths") or [])
    kinds = core.clean_kinds(raw.get("kinds") or [])
    ticket_kinds = core.clean_ticket_kinds(raw.get("ticket_kinds") or [])
    evidence = core.clean_evidence(raw.get("evidence") or [])
    created = {
        (item["kind"], item["ref"]): _timestamp(item.get("created_at"))
        for item in raw.get("evidence") or []
        if isinstance(item, dict)
    }
    evidence_rows = [
        {
            "kind": item["kind"],
            "ref": item["ref"],
            "created_at": created.get((item["kind"], item["ref"]), now()),
        }
        for item in evidence
    ]
    files = core.clean_files(raw.get("files") or [], cfg)
    return {
        "code": code,
        "scope": scope,
        "kind": kind,
        "key": key,
        "title": title,
        "description": description,
        "content": content,
        "paths": paths,
        "kinds": kinds,
        "ticket_kinds": ticket_kinds,
        "status": status,
        "confidence": core.confidence(raw.get("confidence", 0.5)),
        "source": source,
        "actor": None if raw.get("actor") is None else str(raw["actor"])[:200],
        "session_id": (
            None if raw.get("session_id") is None else str(raw["session_id"])[:200]
        ),
        "ticket_id": None if raw.get("ticket_id") is None else str(raw["ticket_id"]),
        "supersedes_code": raw.get("supersedes_code") or None,
        "superseded_by": raw.get("superseded_by") or None,
        "pinned": bool(raw.get("pinned")),
        "locked": bool(raw.get("locked")),
        "use_count": max(0, int(raw.get("use_count") or 0)),
        "helpful_count": max(0, int(raw.get("helpful_count") or 0)),
        "unhelpful_count": max(0, int(raw.get("unhelpful_count") or 0)),
        "last_used_at": raw.get("last_used_at") or None,
        "last_briefed_at": raw.get("last_briefed_at") or None,
        "created_at": _timestamp(raw.get("created_at")),
        "updated_at": _timestamp(raw.get("updated_at")),
        "archived_at": raw.get("archived_at") or None,
        "fingerprint": normalize_fingerprint(title, content),
        "evidence": evidence_rows,
        "files": files,
    }


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


def rollback(
    conn: sqlite3.Connection,
    *,
    ledger_id: int | None = None,
    snapshot: str | Path | None = None,
    dry_run: bool = False,
    actor: str | None = None,
    session_id: str | None = None,
    reason: str | None = None,
) -> dict[str, Any]:
    """Revierte por asiento de ledger o por snapshot, con dry-run opcional."""
    if (ledger_id is None) == (snapshot is None):
        raise ValidationError(
            "Indica un ledger-id o --snapshot, exactamente uno"
        )
    if snapshot is not None:
        result = _import(
            conn,
            snapshot,
            strategy="replace",
            dry_run=dry_run,
            action="rollback",
            exact=True,
            actor=actor,
            session_id=session_id,
            reason=reason,
        )
        result["mode"] = "snapshot"
        return result
    assert ledger_id is not None
    if dry_run:
        conn.execute("SAVEPOINT memory_rollback_dry_run")
    try:
        result = ledger_lib.rollback(
            conn,
            int(ledger_id),
            actor=actor,
            session_id=session_id,
            reason=reason or f"rollback del asiento {ledger_id}",
        )
    finally:
        if dry_run:
            conn.execute("ROLLBACK TO memory_rollback_dry_run")
            conn.execute("RELEASE memory_rollback_dry_run")
    result["mode"] = "ledger"
    result["dry_run"] = bool(dry_run)
    return result


__all__ = ["VERSION", "export_snapshot", "import_snapshot", "rollback"]
