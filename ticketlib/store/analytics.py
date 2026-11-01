"""Analítica, estadísticas y exportación de tickets."""

from __future__ import annotations

import sqlite3
from typing import Any, Iterable

from .. import db
from ..domain import (
    KINDS,
    OPEN_STATUSES,
    PRIORITIES,
    STATUSES,
)
from ..util import now
from .queries import get_ticket, list_tickets

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
    """Exporta el universo íntegro de tickets (incluyendo archivados) y metadatos."""
    rows = list_tickets(
        conn, include_archived=True, include_scratch=True, limit=None
    )
    return {
        "schema_version": db.meta_get(conn, "schema_version"),
        "exported_at": now(),
        "tickets": [get_ticket(conn, row["code"]) for row in rows],
    }
