"""Tickets *scratch*: trabajo temporal que no ensucia la tabla.

Un scratch es un ticket normal con la etiqueta ``scratch`` (más
``sesion:<id>``) y una caducidad guardada en ``meta``. El guard lo acepta como
ticket activo, pero ``list``/``stats``/``next``/TUI/audit lo ocultan y
``confirm`` lo rechaza: para que cuente hay que promoverlo.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta
from typing import Any, Iterable

from . import db, repo
from .domain import SCRATCH_SESSION_PREFIX, SCRATCH_TAG
from .util import now

SESSION_PREFIX = SCRATCH_SESSION_PREFIX
META_PREFIX = "_scratch:"
DEFAULT_TTL = 120  # minutos
DEFAULT_PROJECT = "scratch"


def session_tag(session_id: str | None) -> str | None:
    value = (session_id or "").strip().lower()
    return f"{SESSION_PREFIX}{value}" if value else None


def is_scratch(ticket: dict[str, Any]) -> bool:
    """``True`` si el ticket está marcado como temporal."""
    return SCRATCH_TAG in (ticket.get("tags") or [])


def _meta_key(code: str) -> str:
    return f"{META_PREFIX}{code}"


def deadline(ttl: int = DEFAULT_TTL) -> str:
    """Instante de caducidad en ISO local (``ttl`` en minutos)."""
    return (
        datetime.now().astimezone() + timedelta(minutes=max(1, int(ttl)))
    ).isoformat(timespec="seconds")


def expires_at(conn: sqlite3.Connection, code: str) -> str | None:
    """Caducidad guardada de un scratch, o ``None`` si no tiene."""
    return db.meta_get(conn, _meta_key(code))


def expired(conn: sqlite3.Connection, at: str | None = None) -> list[dict[str, Any]]:
    """Scratch cuyo plazo ya venció (los archivados no cuentan)."""
    moment = at or now()
    rows = conn.execute(
        "SELECT t.id, t.code FROM tickets t "
        "JOIN tags tg ON tg.ticket_id = t.id AND tg.tag = ? "
        "WHERE t.archived = 0 ORDER BY t.id",
        (SCRATCH_TAG,),
    )
    result: list[dict[str, Any]] = []
    for row in rows:
        limit = expires_at(conn, row["code"])
        if limit and limit <= moment:
            result.append(dict(row))
    return result


def clean(conn: sqlite3.Connection, at: str | None = None) -> list[str]:
    """Archiva los scratch caducados y devuelve sus códigos."""
    moment = at or now()
    codes = [row["code"] for row in expired(conn, moment)]
    if not codes:
        return []
    ts = moment
    with db.tx(conn):
        for code in codes:
            row = conn.execute(
                "SELECT id FROM tickets WHERE code = ? AND archived = 0", (code,)
            ).fetchone()
            if row is None:
                continue
            conn.execute(
                "UPDATE tickets SET archived = 1, updated_at = ? WHERE id = ?",
                (ts, row["id"]),
            )
            conn.execute("DELETE FROM meta WHERE key = ?", (_meta_key(code),))
    return codes


def _find_alive(
    conn: sqlite3.Connection,
    agent: str,
    session_id: str | None,
) -> dict[str, Any] | None:
    """Scratch vivo del agente/sesión (sin caducar)."""
    tag = session_tag(session_id)
    condition = ""
    params: list[Any] = [SCRATCH_TAG, agent]
    if tag:
        condition = (
            " AND EXISTS (SELECT 1 FROM tags ts WHERE ts.ticket_id = t.id "
            "AND ts.tag = ?)"
        )
        params.append(tag)
    row = conn.execute(
        "SELECT t.code FROM tickets t "
        "JOIN tags tg ON tg.ticket_id = t.id AND tg.tag = ? "
        "WHERE t.archived = 0 AND t.status = 'en_progreso' "
        "AND t.assigned_to = ?"
        + condition
        + " ORDER BY t.updated_at DESC, t.id DESC LIMIT 1",
        tuple(params),
    ).fetchone()
    if row is None:
        return None
    limit = expires_at(conn, row["code"])
    if limit and limit <= now():
        return None
    return repo.get_ticket(conn, row["code"])


def create_or_reuse(
    conn: sqlite3.Connection,
    *,
    agent: str = "opencode",
    session_id: str | None = None,
    title: str | None = None,
    ttl: int = DEFAULT_TTL,
) -> dict[str, Any]:
    """Devuelve un scratch vivo de la sesión o crea uno nuevo."""
    reused = _find_alive(conn, agent, session_id)
    if reused is not None:
        return reused
    clean(conn)

    tags = [SCRATCH_TAG]
    tag = session_tag(session_id)
    if tag:
        tags.append(tag)
    ticket = repo.create_ticket(
        conn,
        title=(title or f"Scratch de {agent}").strip()[:120],
        body=(
            "Ticket temporal para pruebas o exploración. No aparece en la "
            "tabla ni en las métricas; se archiva al caducar "
            f"({DEFAULT_TTL} min por defecto)."
        ),
        kind="otro",
        priority="media",
        project=DEFAULT_PROJECT,
        tags=tags,
        created_by=agent,
        actor_type="agente",
        session_id=session_id,
        assigned_to=agent,
    )
    repo.claim_ticket(
        conn, ticket["code"], agent=agent, session_id=session_id
    )
    with db.tx(conn):
        db.meta_set(conn, _meta_key(ticket["code"]), deadline(ttl))
    return repo.get_ticket(conn, ticket["code"])


def promote(conn: sqlite3.Connection, code: str) -> dict[str, Any]:
    """Quita la marca de scratch para que el ticket vuelva a la tabla."""
    ticket = repo.get_ticket(conn, code)
    if not is_scratch(ticket):
        return ticket
    with db.tx(conn):
        conn.execute(
            "DELETE FROM tags WHERE ticket_id = ? AND tag = ?",
            (ticket["id"], SCRATCH_TAG),
        )
        conn.execute(
            "DELETE FROM tags WHERE ticket_id = ? AND substr(tag, 1, ?) = ?",
            (ticket["id"], len(SESSION_PREFIX), SESSION_PREFIX),
        )
        conn.execute("DELETE FROM meta WHERE key = ?", (_meta_key(ticket["code"]),))
    return repo.get_ticket(conn, ticket["code"])


def drop(conn: sqlite3.Connection, code: str) -> dict[str, Any]:
    """Archiva un scratch sin pasar por la tabla."""
    ticket = repo.get_ticket(conn, code)
    if not is_scratch(ticket):
        return ticket
    with db.tx(conn):
        conn.execute(
            "UPDATE tickets SET archived = 1, updated_at = ? WHERE id = ?",
            (now(), ticket["id"]),
        )
        conn.execute("DELETE FROM meta WHERE key = ?", (_meta_key(ticket["code"]),))
    return repo.get_ticket(conn, code)


def list_scratch(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    """Scratch visibles (incluye caducados aún no limpiados)."""
    rows = conn.execute(
        "SELECT t.code FROM tickets t "
        "JOIN tags tg ON tg.ticket_id = t.id AND tg.tag = ? "
        "WHERE t.archived = 0 ORDER BY t.id DESC",
        (SCRATCH_TAG,),
    )
    return [repo.get_ticket(conn, row["code"]) for row in rows]
