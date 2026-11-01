"""Contexto de sesión: cadena de ancestros, ticket activo y épica."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .. import sessions

def session_chain(session_id: str | None) -> list[str]:
    """Sesión y sus ancestros (los subagentes heredan el ticket del padre)."""
    if not session_id:
        return []
    chain = [session_id]
    seen = {session_id}
    parent = sessions.session_parent(session_id)
    while parent and parent not in seen and len(chain) < 6:
        chain.append(parent)
        seen.add(parent)
        parent = sessions.session_parent(parent)
    return chain


def _placeholders(items: list[str]) -> str:
    return ", ".join("?" for _ in items)


def find_active(
    conn: Any, chain: list[str], agent: str
) -> dict[str, Any] | None:
    """Ticket en progreso de la sesión (o asignado al agente sin sesión)."""
    if chain:
        row = conn.execute(
            "SELECT * FROM tickets WHERE archived = 0 AND status = 'en_progreso' "
            "AND owner_session IN ("
            + _placeholders(chain)
            + ") ORDER BY updated_at DESC LIMIT 1",
            tuple(chain),
        ).fetchone()
    else:
        row = None
    if row is None and chain:
        row = conn.execute(
            "SELECT * FROM tickets WHERE archived = 0 AND status = 'en_progreso' "
            "AND owner_session IS NULL AND id IN ("
            "  SELECT ticket_id FROM events WHERE session_id IN ("
            + _placeholders(chain)
            + ")) ORDER BY updated_at DESC LIMIT 1",
            tuple(chain),
        ).fetchone()
    if row is None and not chain:
        row = conn.execute(
            "SELECT * FROM tickets WHERE archived = 0 AND status = 'en_progreso' "
            "AND assigned_to = ? ORDER BY updated_at DESC LIMIT 1",
            (agent,),
        ).fetchone()
    return dict(row) if row else None


def find_pending(conn: Any, chain: list[str]) -> dict[str, Any] | None:
    """Ticket nuevo/pendiente que ya creó esta sesión (evita duplicados)."""
    if not chain:
        return None
    row = conn.execute(
        "SELECT t.* FROM tickets t JOIN events e ON e.ticket_id = t.id "
        "WHERE e.session_id IN ("
        + _placeholders(chain)
        + ") AND e.action = 'crear' AND t.archived = 0 "
        "AND t.status IN ('nuevo', 'pendiente') "
        "ORDER BY t.id DESC LIMIT 1",
        tuple(chain),
    ).fetchone()
    return dict(row) if row else None


def find_session_epic(
    conn: Any,
    chain: list[str],
    project: str | None = None,
) -> dict[str, Any] | None:
    """Épica abierta de la sesión a la que colgar el trabajo nuevo.

    Una épica solo se considera cerrada cuando se **confirma**: mientras siga
    en ``en_progreso`` o ``completado`` admite tareas nuevas. Se exige que ya
    tenga tareas (un ticket suelto no es una épica) y, si se conoce, el mismo
    proyecto.
    """
    if not chain:
        return None
    conditions = [
        "t.archived = 0",
        "t.parent_id IS NULL",
        "t.status IN ('en_progreso', 'completado')",
        "EXISTS (SELECT 1 FROM tickets c "
        "WHERE c.parent_id = t.id AND c.archived = 0)",
        "t.id IN (SELECT ticket_id FROM events WHERE session_id IN ("
        + _placeholders(chain)
        + "))",
    ]
    params: list[Any] = list(chain)
    if project:
        conditions.append("(t.project IS NULL OR t.project = ?)")
        params.append(project)
    row = conn.execute(
        "SELECT t.* FROM tickets t WHERE "
        + " AND ".join(conditions)
        + " ORDER BY t.updated_at DESC, t.id DESC LIMIT 1",
        tuple(params),
    ).fetchone()
    return dict(row) if row else None


def auto_title(session_id: str | None, agent: str, base: Path) -> str:
    """Título del ticket automático: el de la sesión, o uno derivado."""
    if session_id:
        info = sessions.session_info([session_id]).get(session_id) or {}
        title = " ".join(str(info.get("title") or "").split())
        if title:
            return title[:120]
    label = base.name or "tickets"
    return f"Trabajo de {agent} en {label}"
