"""Filas, códigos y eventos compartidos por el repositorio."""

from __future__ import annotations

import json
import sqlite3
from typing import Any

from ..domain import DEFAULT_ISOLATION, NotFoundError
from ..util import normalize_code, now

UNSET = object()


def _decode_list(raw: Any) -> list[Any]:
    if isinstance(raw, str) and raw.strip():
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            parsed = [raw]
        return parsed if isinstance(parsed, list) else [str(parsed)]
    return []


def row_to_ticket(row: sqlite3.Row) -> dict[str, Any]:
    data = dict(row)
    tags_csv = data.pop("tags_csv", None)
    data["archived"] = bool(data["archived"])
    data["tags"] = [tag for tag in (tags_csv or "").split(",") if tag]
    data["paths"] = _decode_list(data.get("paths"))
    data.setdefault("isolation", DEFAULT_ISOLATION)
    data["resources"] = _decode_list(data.get("resources"))
    raw_reversal = data.get("reversal")
    if isinstance(raw_reversal, str) and raw_reversal.strip():
        try:
            parsed_reversal = json.loads(raw_reversal)
        except json.JSONDecodeError:
            parsed_reversal = None
        data["reversal"] = (
            parsed_reversal if isinstance(parsed_reversal, dict) else None
        )
    else:
        data["reversal"] = None
    return data


def row_by_code(conn: sqlite3.Connection, code: Any) -> sqlite3.Row:
    key = normalize_code(code)
    if isinstance(key, int):
        row = conn.execute("SELECT * FROM tickets WHERE id = ?", (key,)).fetchone()
    else:
        row = conn.execute("SELECT * FROM tickets WHERE code = ?", (key,)).fetchone()
    if row is None:
        raise NotFoundError(f"Ticket no encontrado: {code}")
    return row


def event(
    conn: sqlite3.Connection,
    ticket_id: int,
    action: str,
    actor: str,
    actor_type: str = "agente",
    session_id: str | None = None,
    from_status: str | None = None,
    to_status: str | None = None,
    note: str | None = None,
) -> None:
    conn.execute(
        "INSERT INTO events(ticket_id, ts, actor, actor_type, session_id, action, "
        "from_status, to_status, note) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            ticket_id,
            now(),
            actor,
            actor_type,
            session_id,
            action,
            from_status,
            to_status,
            note,
        ),
    )
