"""Ciclo de vida de memorias: patch, estados, feedback y supersesión."""

from __future__ import annotations

import json
import sqlite3
from typing import Any

from .. import db
from ..domain import ValidationError
from ..store.common import UNSET
from ..util import check_enum, now
from . import core
from . import ledger as ledger_lib
from .models import MEMORY_KINDS, normalize_fingerprint


def patch_memory(
    conn: sqlite3.Connection,
    code: Any,
    *,
    title: Any = UNSET,
    description: Any = UNSET,
    content: Any = UNSET,
    key: Any = UNSET,
    kind: Any = UNSET,
    confidence: Any = UNSET,
    paths: Any = UNSET,
    kinds: Any = UNSET,
    actor: str | None = None,
    session_id: str | None = None,
    reason: str | None = None,
    config: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Actualiza campos editables con validación, dedupe y ledger."""
    cfg = core.config(config)
    row = core.memory_row(conn, code)
    current = dict(row)
    scope = str(current["scope"])
    updates: dict[str, Any] = {}

    if title is not UNSET:
        updates["title"] = core.clean_text(
            title, "título", int(cfg["max_entry_chars"])
        )
    if description is not UNSET:
        updates["description"] = core.clean_text(
            description,
            "descripción",
            int(cfg["max_entry_chars"]),
            required=False,
        )
    if content is not UNSET:
        updates["content"] = core.clean_text(
            content, "contenido", int(cfg["max_entry_chars"])
        )
    if key is not UNSET:
        key_text = str(key or "").strip()
        if not key_text:
            raise ValidationError("La clave de la memoria no puede quedar vacía")
        updates["key"] = key_text
    if kind is not UNSET:
        check_enum(kind, MEMORY_KINDS, "kind")
        updates["kind"] = kind
    if confidence is not UNSET:
        updates["confidence"] = core.confidence(confidence)
    if paths is not UNSET:
        project_paths = core.clean_project_paths(paths)
        updates["paths"] = (
            json.dumps(project_paths, ensure_ascii=False) if project_paths else None
        )
    if kinds is not UNSET:
        kind_list = core.clean_kinds(kinds)
        updates["kinds"] = (
            json.dumps(kind_list, ensure_ascii=False) if kind_list else None
        )

    if not updates:
        return core.receipt(conn, row, cfg)

    final_title = updates.get("title", current["title"])
    final_content = updates.get("content", current["content"])
    fingerprint = normalize_fingerprint(final_title, final_content)
    updates["fingerprint"] = fingerprint
    updates["updated_at"] = now()

    final_key = updates.get("key", current["key"])
    if "key" in updates:
        clash = conn.execute(
            'SELECT * FROM memories WHERE scope = ? AND "key" = ? AND id != ? '
            "AND status IN ('active', 'proposed', 'stale') LIMIT 1",
            (scope, final_key, current["id"]),
        ).fetchone()
        if clash is not None:
            raise ValidationError(
                f"Ya existe la memoria {clash['code']} con la clave {final_key!r}; "
                "usa supersede_memory o una clave distinta."
            )

    duplicate = conn.execute(
        "SELECT * FROM memories WHERE scope = ? AND fingerprint = ? AND id != ? "
        "AND status IN ('active', 'proposed', 'stale') LIMIT 1",
        (scope, fingerprint, current["id"]),
    ).fetchone()
    if duplicate is not None:
        raise ValidationError(
            f"Memoria duplicada: {duplicate['code']} ya tiene ese contenido."
        )

    columns = ", ".join(f'"{name}" = ?' for name in updates)
    values = list(updates.values()) + [current["id"]]
    with db.tx(conn):
        before = ledger_lib.snapshot(conn, int(current["id"]))
        conn.execute(f"UPDATE memories SET {columns} WHERE id = ?", values)
        after = ledger_lib.snapshot(conn, int(current["id"]))
        ledger_lib.record(
            conn,
            action="update",
            memory_id=int(current["id"]),
            before=before,
            after=after,
            actor=actor,
            session_id=session_id,
            reason=reason,
        )
        core.check_budget(conn, scope, cfg)
        updated = core.memory_row(conn, code)
        return core.receipt(conn, updated, cfg)


def _transition(
    conn: sqlite3.Connection,
    code: Any,
    *,
    status: str,
    action: str,
    allowed: tuple[str, ...],
    actor: str | None,
    session_id: str | None,
    reason: str | None,
    config: dict[str, Any] | None,
    archived: bool = False,
) -> dict[str, Any]:
    cfg = core.config(config)
    row = core.memory_row(conn, code)
    if row["status"] not in allowed:
        raise ValidationError(
            f"La memoria {row['code']} está en estado {row['status']!r}; "
            f"la acción {action} requiere {', '.join(allowed)}."
        )
    timestamp = now()
    with db.tx(conn):
        before = ledger_lib.snapshot(conn, int(row["id"]))
        if archived:
            conn.execute(
                "UPDATE memories SET status = ?, archived_at = ?, updated_at = ? "
                "WHERE id = ?",
                (status, timestamp, timestamp, int(row["id"])),
            )
        else:
            conn.execute(
                "UPDATE memories SET status = ?, archived_at = NULL, updated_at = ? "
                "WHERE id = ?",
                (status, timestamp, int(row["id"])),
            )
        after = ledger_lib.snapshot(conn, int(row["id"]))
        ledger_lib.record(
            conn,
            action=action,
            memory_id=int(row["id"]),
            before=before,
            after=after,
            actor=actor,
            session_id=session_id,
            reason=reason,
        )
        core.check_budget(conn, str(row["scope"]), cfg)
        updated = core.memory_row(conn, code)
        return core.receipt(conn, updated, cfg)


def approve_memory(
    conn: sqlite3.Connection,
    code: Any,
    *,
    actor: str | None = None,
    session_id: str | None = None,
    reason: str | None = None,
    config: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Aprobación humana: proposed -> active."""
    return _transition(
        conn,
        code,
        status="active",
        action="approve",
        allowed=("proposed",),
        actor=actor,
        session_id=session_id,
        reason=reason,
        config=config,
    )


def reject_memory(
    conn: sqlite3.Connection,
    code: Any,
    *,
    actor: str | None = None,
    session_id: str | None = None,
    reason: str | None = None,
    config: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Rechazo: proposed -> archived (el histórico queda en ledger)."""
    return _transition(
        conn,
        code,
        status="archived",
        action="reject",
        allowed=("proposed",),
        actor=actor,
        session_id=session_id,
        reason=reason,
        config=config,
        archived=True,
    )


def archive_memory(
    conn: sqlite3.Connection,
    code: Any,
    *,
    actor: str | None = None,
    session_id: str | None = None,
    reason: str | None = None,
    config: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Archiva una memoria activa o propuesta."""
    return _transition(
        conn,
        code,
        status="archived",
        action="archive",
        allowed=("active", "proposed", "stale"),
        actor=actor,
        session_id=session_id,
        reason=reason,
        config=config,
        archived=True,
    )


def restore_memory(
    conn: sqlite3.Connection,
    code: Any,
    *,
    actor: str | None = None,
    session_id: str | None = None,
    reason: str | None = None,
    config: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Restaura una memoria archivada a activa."""
    return _transition(
        conn,
        code,
        status="active",
        action="restore",
        allowed=("archived",),
        actor=actor,
        session_id=session_id,
        reason=reason,
        config=config,
    )


def pin_memory(
    conn: sqlite3.Connection,
    code: Any,
    *,
    pinned: bool = True,
    actor: str | None = None,
    session_id: str | None = None,
    reason: str | None = None,
    config: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Fija o desfija una memoria para el brief."""
    cfg = core.config(config)
    row = core.memory_row(conn, code)
    timestamp = now()
    with db.tx(conn):
        before = ledger_lib.snapshot(conn, int(row["id"]))
        conn.execute(
            "UPDATE memories SET pinned = ?, updated_at = ? WHERE id = ?",
            (1 if pinned else 0, timestamp, int(row["id"])),
        )
        after = ledger_lib.snapshot(conn, int(row["id"]))
        ledger_lib.record(
            conn,
            action="pin" if pinned else "unpin",
            memory_id=int(row["id"]),
            before=before,
            after=after,
            actor=actor,
            session_id=session_id,
            reason=reason,
        )
        updated = core.memory_row(conn, code)
        return core.receipt(conn, updated, cfg)


def feedback_memory(
    conn: sqlite3.Connection,
    code: Any,
    *,
    helpful: bool,
    note: str | None = None,
    actor: str | None = None,
    session_id: str | None = None,
    config: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Registra feedback y actualiza los contadores de utilidad."""
    cfg = core.config(config)
    row = core.memory_row(conn, code)
    column = "helpful_count" if helpful else "unhelpful_count"
    with db.tx(conn):
        before = ledger_lib.snapshot(conn, int(row["id"]))
        core.record_usage(
            conn,
            int(row["id"]),
            "feedback",
            session_id,
            helpful=helpful,
            note=note,
            touch=False,
        )
        conn.execute(
            f"UPDATE memories SET {column} = {column} + 1, updated_at = ? "
            "WHERE id = ?",
            (now(), int(row["id"])),
        )
        after = ledger_lib.snapshot(conn, int(row["id"]))
        ledger_lib.record(
            conn,
            action="feedback",
            memory_id=int(row["id"]),
            before=before,
            after=after,
            actor=actor,
            session_id=session_id,
            reason=note,
        )
        updated = core.memory_row(conn, code)
        return core.receipt(conn, updated, cfg)


def supersede_memory(
    conn: sqlite3.Connection,
    code: Any,
    by: Any,
    *,
    actor: str | None = None,
    session_id: str | None = None,
    reason: str | None = None,
    config: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Marca ``code`` como superseded por ``by`` y enlaza ambas."""
    cfg = core.config(config)
    old = core.memory_row(conn, code)
    new = core.memory_row(conn, by)
    if int(old["id"]) == int(new["id"]):
        raise ValidationError("Una memoria no puede sustituirse a sí misma")
    if old["status"] == "superseded":
        raise ValidationError(f"La memoria {old['code']} ya está superseded")
    if new["status"] in ("archived", "superseded"):
        raise ValidationError(f"La memoria sustituta {new['code']} no está vigente")
    timestamp = now()
    with db.tx(conn):
        before = ledger_lib.snapshot(conn, int(old["id"]))
        conn.execute(
            "UPDATE memories SET status = 'superseded', superseded_by = ?, "
            "updated_at = ? WHERE id = ?",
            (new["code"], timestamp, int(old["id"])),
        )
        conn.execute(
            "UPDATE memories SET supersedes_id = COALESCE(supersedes_id, ?) "
            "WHERE id = ?",
            (int(old["id"]), int(new["id"])),
        )
        after = ledger_lib.snapshot(conn, int(old["id"]))
        ledger_lib.record(
            conn,
            action="supersede",
            memory_id=int(old["id"]),
            before=before,
            after=after,
            actor=actor,
            session_id=session_id,
            reason=reason or f"sustituida por {new['code']}",
            meta={"by": new["code"]},
        )
        updated = core.memory_row(conn, old["code"])
        return core.receipt(conn, updated, cfg)


__all__ = [
    "approve_memory",
    "archive_memory",
    "feedback_memory",
    "patch_memory",
    "pin_memory",
    "reject_memory",
    "restore_memory",
    "supersede_memory",
]
