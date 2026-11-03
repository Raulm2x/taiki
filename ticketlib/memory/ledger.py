"""Ledger append-only y rollback de memorias."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from typing import Any

from .. import db
from ..domain import ValidationError
from ..util import now
from . import budget as budget_lib
from . import security
from .config import resolve_memory_config
from .models import BUDGET_STATUSES

ACTOR_TYPE = "agente"


def canonical(value: Any) -> str:
    """JSON estable para hashear un snapshot sin depender del orden."""
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )


def store_blob(conn: sqlite3.Connection, content: str) -> str:
    """Guarda contenido content-addressed y devuelve su sha256."""
    digest = hashlib.sha256(content.encode("utf-8")).hexdigest()
    conn.execute(
        "INSERT OR IGNORE INTO memory_blobs(sha256, content, created_at) "
        "VALUES (?, ?, ?)",
        (digest, content, now()),
    )
    return digest


def snapshot(conn: sqlite3.Connection, memory_id: int) -> dict[str, Any] | None:
    """Estado completo de una memoria: fila, evidencias y ficheros."""
    row = conn.execute("SELECT * FROM memories WHERE id = ?", (memory_id,)).fetchone()
    if row is None:
        return None
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
    return {"memory": dict(row), "evidence": evidence, "files": files}


def record(
    conn: sqlite3.Connection,
    *,
    action: str,
    memory_id: int | None = None,
    before: dict[str, Any] | None = None,
    after: dict[str, Any] | None = None,
    actor: str | None = None,
    actor_type: str = ACTOR_TYPE,
    session_id: str | None = None,
    reason: str | None = None,
    meta: dict[str, Any] | None = None,
) -> int:
    """Añade un asiento y devuelve su id."""
    safe_actor = (
        security.scan_content(actor, max_chars=200, field="actor")
        if actor is not None
        else None
    )
    safe_session = (
        security.scan_content(session_id, max_chars=200, field="session_id")
        if session_id is not None
        else None
    )
    safe_reason = (
        security.scan_content(reason, max_chars=2000, field="motivo")
        if reason is not None
        else None
    )
    serialized_meta = canonical(meta) if meta is not None else None
    if serialized_meta is not None:
        security.scan_content(serialized_meta, max_chars=2000, field="meta del ledger")
    before_hash = store_blob(conn, canonical(before)) if before is not None else None
    after_hash = store_blob(conn, canonical(after)) if after is not None else None
    cursor = conn.execute(
        "INSERT INTO memory_ledger("
        "ts, actor, actor_type, session_id, action, memory_id, "
        "before_hash, after_hash, reason, meta"
        ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            now(),
            safe_actor,
            actor_type,
            safe_session,
            action,
            memory_id,
            before_hash,
            after_hash,
            safe_reason,
            serialized_meta,
        ),
    )
    return int(cursor.lastrowid or 0)


def _quote(column: str) -> str:
    return '"' + column.replace('"', '""') + '"'


def _restore_one(conn: sqlite3.Connection, target: dict[str, Any]) -> None:
    """Restaura una fila y sus relaciones sin borrar el resto de memorias."""
    data = target.get("memory")
    if not isinstance(data, dict) or not data:
        return
    memory_id = int(data["id"])
    existing = conn.execute(
        "SELECT 1 FROM memories WHERE id = ?", (memory_id,)
    ).fetchone()
    columns = list(data)
    if existing:
        assignments = ", ".join(f"{_quote(name)} = ?" for name in columns if name != "id")
        values = [data[name] for name in columns if name != "id"]
        conn.execute(
            f"UPDATE memories SET {assignments} WHERE id = ?",
            (*values, memory_id),
        )
    else:
        names = ", ".join(_quote(name) for name in columns)
        placeholders = ", ".join("?" for _ in columns)
        conn.execute(
            f"INSERT INTO memories({names}) VALUES ({placeholders})",
            tuple(data[name] for name in columns),
        )

    conn.execute("DELETE FROM memory_evidence WHERE memory_id = ?", (memory_id,))
    for item in target.get("evidence", []):
        conn.execute(
            "INSERT OR IGNORE INTO memory_evidence(memory_id, kind, ref, created_at) "
            "VALUES (?, ?, ?, ?)",
            (memory_id, item.get("kind"), item.get("ref"), item.get("created_at") or now()),
        )
    conn.execute("DELETE FROM memory_files WHERE memory_id = ?", (memory_id,))
    for item in target.get("files", []):
        conn.execute(
            "INSERT OR REPLACE INTO memory_files(memory_id, path, content, sha256) "
            "VALUES (?, ?, ?, ?)",
            (
                memory_id,
                item.get("path"),
                item.get("content"),
                item.get("sha256"),
            ),
        )


def _restore(conn: sqlite3.Connection, target: dict[str, Any]) -> None:
    """Restaura el snapshot principal y, si existe, su contraparte."""
    _restore_one(conn, target)
    counterpart = target.get("counterpart")
    if isinstance(counterpart, dict):
        _restore_one(conn, counterpart)


def _budget_config(value: dict[str, Any] | None) -> dict[str, Any]:
    return resolve_memory_config(value)


def _check_budget(
    conn: sqlite3.Connection,
    scope: str,
    cfg: dict[str, Any],
) -> None:
    rows = conn.execute(
        "SELECT * FROM memories WHERE scope = ? AND status IN (?, ?, ?)",
        (scope, *BUDGET_STATUSES),
    ).fetchall()
    limit = int(
        cfg["user_budget_chars"] if scope == "user" else cfg["project_budget_chars"]
    )
    budget_lib.enforce([dict(row) for row in rows], limit, scope)


def _snapshot_scopes(snapshot: dict[str, Any] | None) -> set[str]:
    if not snapshot:
        return set()
    scopes: set[str] = set()
    for key in ("memory", "counterpart"):
        data = snapshot.get(key)
        if isinstance(data, dict) and isinstance(data.get("memory"), dict):
            data = data["memory"]
        if isinstance(data, dict) and data.get("scope"):
            scopes.add(str(data["scope"]))
    return scopes


def rollback(
    conn: sqlite3.Connection,
    ledger_id: int,
    *,
    actor: str | None = None,
    session_id: str | None = None,
    reason: str | None = None,
    config: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Restaura el estado anterior al asiento y registra uno nuevo."""
    row = conn.execute(
        "SELECT * FROM memory_ledger WHERE id = ?", (ledger_id,)
    ).fetchone()
    if row is None:
        raise ValidationError(f"Asiento de ledger no encontrado: {ledger_id}")
    memory_id = row["memory_id"]
    if memory_id is None:
        raise ValidationError(
            f"El asiento {ledger_id} no está asociado a una memoria"
        )

    current = snapshot(conn, int(memory_id))
    target: dict[str, Any] | None = None
    if row["before_hash"]:
        blob = conn.execute(
            "SELECT content FROM memory_blobs WHERE sha256 = ?",
            (row["before_hash"],),
        ).fetchone()
        if blob is None:
            raise ValidationError(
                f"Falta el blob {row['before_hash']} del asiento {ledger_id}"
            )
        loaded = json.loads(blob["content"])
        target = loaded if isinstance(loaded, dict) else None

    cfg = _budget_config(config)
    scopes = _snapshot_scopes(current) | _snapshot_scopes(target)
    with db.tx(conn):
        if target is None:
            conn.execute("DELETE FROM memories WHERE id = ?", (memory_id,))
        else:
            _restore(conn, target)
        for scope in sorted(scopes):
            _check_budget(conn, scope, cfg)
        new_id = record(
            conn,
            action="rollback",
            memory_id=int(memory_id),
            before=current,
            after=target,
            actor=actor,
            session_id=session_id,
            reason=reason or f"rollback del asiento {ledger_id}",
            meta={"ledger_id": ledger_id},
        )

    code = None
    if target and isinstance(target.get("memory"), dict):
        code = target["memory"].get("code")
    return {
        "ledger_id": new_id,
        "rolled_back": ledger_id,
        "memory_id": int(memory_id),
        "code": code,
        "restored": target is not None,
    }


__all__ = ["canonical", "record", "rollback", "snapshot", "store_blob"]
