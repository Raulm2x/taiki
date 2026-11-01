"""Capa de datos de la TUI: sondeo barato y snapshots completos.

El refresco automático no debe releer toda la base cada dos segundos. Se
consulta una huella mínima (``MAX(updated_at)``, ``MAX(events.id)`` y
``MAX(tickets.id)``) y solo se recarga cuando cambia, o cada cierto tiempo para
revalidar sesiones vivas.
"""

from __future__ import annotations

import sqlite3
import time
from dataclasses import dataclass, field
from typing import Any

from ticketlib import repo, servers, sessions

STAMP_SQL = (
    "SELECT (SELECT MAX(updated_at) FROM tickets), "
    "(SELECT MAX(id) FROM events), "
    "(SELECT MAX(id) FROM tickets)"
)


@dataclass
class Snapshot:
    """Todo lo que la TUI necesita para dibujar un fotograma."""

    tickets: list[dict[str, Any]] = field(default_factory=list)
    session_map: dict[str, str | None] = field(default_factory=dict)
    session_dirs: dict[str, str | None] = field(default_factory=dict)
    truncated: bool = False
    stats: dict[str, Any] = field(default_factory=dict)
    activity: list[dict[str, Any]] = field(default_factory=list)
    search_codes: set[str] | None = None
    heatmap: dict[str, dict[str, int]] = field(default_factory=dict)
    mcp_stale: bool = False
    mcp_orphans: int = 0
    stamp: tuple[Any, ...] = ()

    def session_stats(self) -> tuple[int, int]:
        """(sesiones vivas, sesiones registradas) sobre los tickets cargados."""
        alive = sum(1 for sid in self.session_map.values() if sid)
        registered = sum(1 for ticket in self.tickets if ticket.get("has_session"))
        return alive, registered


def db_stamp(conn: sqlite3.Connection) -> tuple[Any, ...]:
    """Huella barata de la base: cambios de tickets o de eventos."""
    row = conn.execute(STAMP_SQL).fetchone()
    return tuple(row)


def load_snapshot(
    conn: sqlite3.Connection,
    limit: int = 300,
    order: str = "code",
    query: str | None = None,
    project: str | None = None,
    agent: str | None = None,
) -> Snapshot:
    """Lee tickets, sesiones, métricas, actividad y códigos que coinciden."""
    tickets = repo.list_tickets(conn, limit=limit, order=order, detail=True)
    activity = repo.recent_events(conn, limit=40)
    stats = repo.stats(conn)
    heatmap = repo.activity_by_day(conn, days=371, project=project, agent=agent)
    fleet = servers.inventory(conn)
    mcp_stale = any(item["state"] == "desactualizado" for item in fleet)
    mcp_orphans = sum(1 for item in fleet if item["state"] == "huérfano")
    search_codes = repo.search_codes(conn, query) if query else None
    info = sessions.ticket_activity(conn, [ticket["code"] for ticket in tickets])
    sessions.decorate(tickets, info)
    session_map = {code: data["session"] for code, data in info.items()}
    session_dirs = {code: data.get("directory") for code, data in info.items()}
    return Snapshot(
        tickets=tickets,
        session_map=session_map,
        session_dirs=session_dirs,
        truncated=len(tickets) >= limit,
        stats=stats,
        activity=activity,
        search_codes=search_codes,
        heatmap=heatmap,
        mcp_stale=mcp_stale,
        mcp_orphans=mcp_orphans,
        stamp=db_stamp(conn),
    )


class DataSource:
    """Decide cuándo merece la pena recargar el snapshot."""

    def __init__(
        self,
        limit: int = 300,
        order: str = "code",
        full_every: float = 20.0,
    ) -> None:
        self.limit = limit
        self.order = order
        self.full_every = full_every
        self.snapshot = Snapshot()
        self.stamp: tuple[Any, ...] | None = None
        self.loaded_at = 0.0
        self.query: str | None = None
        self.project: str | None = None
        self.agent: str | None = None

    def configure(self, limit: int | None = None, order: str | None = None) -> None:
        if limit is not None and limit != self.limit:
            self.limit = limit
            self.stamp = None
        if order is not None and order != self.order:
            self.order = order
            self.stamp = None

    def poll(
        self,
        conn: sqlite3.Connection,
        force: bool = False,
        query: str | None = None,
        now: float | None = None,
        *,
        project: str | None = None,
        agent: str | None = None,
    ) -> Snapshot | None:
        """Devuelve un snapshot nuevo o ``None`` si nada cambió."""
        now = now if now is not None else time.monotonic()
        if query != self.query:
            self.query = query
            force = True
        if (project, agent) != (self.project, self.agent):
            self.project, self.agent = project, agent
            force = True
        stamp = db_stamp(conn)
        expired = now - self.loaded_at >= self.full_every
        if not force and stamp == self.stamp and not expired:
            return None
        self.snapshot = load_snapshot(
            conn,
            limit=self.limit,
            order=self.order,
            query=query,
            project=project,
            agent=agent,
        )
        self.stamp = self.snapshot.stamp
        self.loaded_at = now
        return self.snapshot
