"""Actividad y sesión viva de cada ticket."""

from __future__ import annotations

import sqlite3
from typing import Any, Iterable

from ..agents import GENERIC_ACTORS
from .backends import session_info

def ticket_activity(
    conn: sqlite3.Connection, codes: Iterable[str]
) -> dict[str, dict[str, Any]]:
    """Resuelve sesión viva y último actor de cada ticket.

    Prioridad de sesión: el ``events.session_id`` más reciente que siga
    existiendo (si una sesión fue borrada se prueba con la anterior) y, como
    respaldo, los enlaces ``sesion`` del ticket. El actor es el del evento más
    reciente, aunque no tenga sesión asociada.
    """
    wanted = [code for code in dict.fromkeys(codes) if code]
    if not wanted:
        return {}

    placeholders = ", ".join("?" for _ in wanted)
    candidates: dict[str, list[str]] = {}
    actors: dict[str, str] = {}

    for row in conn.execute(
        "SELECT t.code AS code, e.session_id AS sid, e.actor AS actor "
        "FROM events e JOIN tickets t ON t.id = e.ticket_id "
        f"WHERE t.code IN ({placeholders}) "
        "ORDER BY e.id DESC",
        wanted,
    ):
        code = row["code"]
        # La primera fila de cada código es su evento más reciente; los actores
        # genéricos (hook de git) no deben tapar al agente real.
        if row["actor"] not in GENERIC_ACTORS:
            actors.setdefault(code, row["actor"])
        if row["sid"]:
            candidates.setdefault(code, []).append(row["sid"])

    for row in conn.execute(
        "SELECT t.code AS code, l.ref AS ref FROM links l "
        "JOIN tickets t ON t.id = l.ticket_id "
        f"WHERE t.code IN ({placeholders}) AND l.kind = 'sesion' "
        "ORDER BY l.id DESC",
        wanted,
    ):
        candidates.setdefault(row["code"], []).append(row["ref"])

    alive = session_info(
        {sid for sessions in candidates.values() for sid in sessions}
    )
    activity: dict[str, dict[str, Any]] = {}
    for code in wanted:
        chosen = next(
            (sid for sid in candidates.get(code, []) if sid in alive), None
        )
        activity[code] = {
            "session": chosen,
            "actor": actors.get(code),
            "registered": bool(candidates.get(code)),
            "directory": alive[chosen]["directory"] if chosen else None,
        }
    return activity


def ticket_sessions(
    conn: sqlite3.Connection, codes: Iterable[str]
) -> dict[str, str | None]:
    """Sesión viva de cada ticket (ver ``ticket_activity``)."""
    return {
        code: info["session"]
        for code, info in ticket_activity(conn, codes).items()
    }


def decorate(
    tickets: Iterable[dict[str, Any]],
    activity: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    """Rellena ``last_actor`` cuando el ticket aún no tiene ``assigned_to``.

    Así un ticket recién creado ya muestra el agente real en la tabla y su
    columna AGENTE es clickeable sin necesidad de reclamarlo.
    """
    tickets = list(tickets)
    for ticket in tickets:
        info = activity.get(ticket["code"], {})
        if not ticket.get("assigned_to"):
            ticket["last_actor"] = info.get("actor")
        ticket["has_session"] = bool(info.get("registered"))
    return tickets
