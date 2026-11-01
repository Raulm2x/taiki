"""Analítica, estadísticas y exportación de tickets."""

from __future__ import annotations

import sqlite3
from typing import Any, Iterable

from .. import db
from ..domain import (
    KINDS,
    OPEN_STATUSES,
    PARENT_CONFIRMED_STATUSES,
    PARENT_DONE_STATUSES,
    PRIORITIES,
    STATUSES,
)
from ..util import now
from .common import row_to_ticket

__all__ = [
    "export_all",
    "stats",
]


def stats(conn: sqlite3.Connection) -> dict[str, Any]:
    """Genera métricas consolidadas sobre el estado de la base de tickets.

    Los tickets *scratch* (temporales) quedan fuera de todas las métricas; solo
    se informa su número en ``scratch``.
    """
    visible = (
        "NOT EXISTS (SELECT 1 FROM tags st "
        "WHERE st.ticket_id = tickets.id AND st.tag = '_scratch')"
    )
    total = conn.execute(
        f"SELECT COUNT(*) FROM tickets WHERE {visible}"
    ).fetchone()[0]
    open_count = conn.execute(
        f"SELECT COUNT(*) FROM tickets WHERE status IN "
        f"({', '.join('?' for _ in OPEN_STATUSES)}) AND {visible}",
        OPEN_STATUSES,
    ).fetchone()[0]

    def group(column: str, universe: Iterable[str] | None = None) -> dict[str, int]:
        counts = {
            row[0]: row[1]
            for row in conn.execute(
                f"SELECT {column}, COUNT(*) FROM tickets "
                f"WHERE {visible} GROUP BY {column}"
            )
        }
        if universe:
            counts = {key: counts.get(key, 0) for key in universe}
        return counts

    projects = {
        (row[0] or "(sin proyecto)"): row[1]
        for row in conn.execute(
            "SELECT project, COUNT(*) FROM tickets "
            f"WHERE {visible} GROUP BY project ORDER BY COUNT(*) DESC"
        )
    }
    sin_agente = conn.execute(
        "SELECT COUNT(*) FROM tickets "
        "WHERE (assigned_to IS NULL OR assigned_to = '') "
        "AND status NOT IN ('confirmado', 'cancelado') "
        f"AND {visible}"
    ).fetchone()[0]
    urgentes_pendientes = conn.execute(
        "SELECT COUNT(*) FROM tickets "
        "WHERE priority = 'urgente' "
        "AND status NOT IN ('confirmado', 'cancelado') "
        f"AND {visible}"
    ).fetchone()[0]
    scratch_count = conn.execute(
        "SELECT COUNT(*) FROM tickets t "
        "JOIN tags tg ON tg.ticket_id = t.id AND tg.tag = '_scratch' "
        "WHERE t.archived = 0"
    ).fetchone()[0]
    return {
        "total": total,
        "abiertos": open_count,
        "sin_agente": sin_agente,
        "urgentes_pendientes": urgentes_pendientes,
        "scratch": scratch_count,
        "por_estado": group("status", STATUSES),
        "por_tipo": group("kind", KINDS),
        "por_prioridad": group("priority", PRIORITIES),
        "por_proyecto": projects,
    }


def export_all(conn: sqlite3.Connection) -> dict[str, Any]:
    """Exporta el universo íntegro de tickets (incluyendo archivados) y metadatos.

    Se resuelve en cinco consultas en vez de seis por ticket: filas, etiquetas,
    comentarios, eventos y enlaces. El resto (commits, subtareas y progreso) se
    deriva en memoria, igual que hace ``get_ticket``.
    """
    tickets = [
        row_to_ticket(row)
        for row in conn.execute("SELECT * FROM tickets ORDER BY id")
    ]
    code_by_id = {ticket["id"]: ticket["code"] for ticket in tickets}

    tags: dict[int, list[str]] = {}
    for row in conn.execute(
        "SELECT ticket_id, tag FROM tags ORDER BY ticket_id, tag"
    ):
        tags.setdefault(row["ticket_id"], []).append(row["tag"])

    comments: dict[int, list[dict[str, Any]]] = {}
    for row in conn.execute(
        "SELECT ticket_id, ts, author, body FROM comments ORDER BY ticket_id, id"
    ):
        comments.setdefault(row["ticket_id"], []).append(
            {"ts": row["ts"], "author": row["author"], "body": row["body"]}
        )

    events: dict[int, list[dict[str, Any]]] = {}
    for row in conn.execute(
        "SELECT ticket_id, ts, actor, actor_type, action, from_status, "
        "to_status, note, session_id FROM events ORDER BY ticket_id, id"
    ):
        events.setdefault(row["ticket_id"], []).append(
            {
                "ts": row["ts"],
                "actor": row["actor"],
                "actor_type": row["actor_type"],
                "action": row["action"],
                "from_status": row["from_status"],
                "to_status": row["to_status"],
                "note": row["note"],
                "session_id": row["session_id"],
            }
        )

    links: dict[int, list[dict[str, Any]]] = {}
    for row in conn.execute(
        "SELECT ticket_id, kind, ref, created_at FROM links "
        "ORDER BY ticket_id, id"
    ):
        links.setdefault(row["ticket_id"], []).append(
            {
                "kind": row["kind"],
                "ref": row["ref"],
                "created_at": row["created_at"],
            }
        )

    children: dict[int, list[dict[str, Any]]] = {}
    for ticket in tickets:
        parent = ticket.get("parent_id")
        if parent is not None:
            children.setdefault(parent, []).append(ticket)

    for ticket in tickets:
        ticket_id = ticket["id"]
        ticket["tags"] = tags.get(ticket_id, [])
        ticket["comments"] = comments.get(ticket_id, [])
        ticket["events"] = events.get(ticket_id, [])
        ticket["links"] = links.get(ticket_id, [])
        commits: list[str] = []
        for link in ticket["links"]:
            if link["kind"] != "commit":
                continue
            ref = link["ref"]
            if any(
                ref == known
                or ref.startswith(known)
                or known.startswith(ref)
                for known in commits
            ):
                continue
            commits.append(ref)
        ticket["commits"] = commits
        ticket["committed"] = bool(commits)
        ticket["parent_code"] = code_by_id.get(ticket.get("parent_id"))
        ticket["subtasks"] = [
            {
                "code": child["code"],
                "title": child["title"],
                "status": child["status"],
                "priority": child["priority"],
                "kind": child["kind"],
            }
            for child in children.get(ticket_id, [])
        ]
        ticket["progress"] = {
            "done": sum(
                1
                for sub in ticket["subtasks"]
                if sub["status"] in PARENT_DONE_STATUSES
            ),
            "confirmed": sum(
                1
                for sub in ticket["subtasks"]
                if sub["status"] in PARENT_CONFIRMED_STATUSES
            ),
            "total": len(ticket["subtasks"]),
        }

    return {
        "schema_version": db.schema_version(conn),
        "exported_at": now(),
        "tickets": tickets,
    }
