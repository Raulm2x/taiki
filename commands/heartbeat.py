"""Comando ``taiki heartbeat``: renueva el lease de un ticket activo."""

from __future__ import annotations

import argparse
import sys

from ticketlib import repo

from .common import _conn


def cmd_heartbeat(args: argparse.Namespace) -> int:
    with _conn() as conn:
        code = (args.code or "").strip()
        if not code:
            active = [
                row["code"]
                for row in conn.execute(
                    "SELECT code FROM tickets WHERE archived = 0 "
                    "AND status = 'en_progreso' ORDER BY updated_at DESC"
                )
            ]
            if len(active) != 1:
                print(
                    "Indica el ticket o deja uno solo en progreso "
                    f"(activos: {', '.join(active) or 'ninguno'})",
                    file=sys.stderr,
                )
                return 1
            code = active[0]
        renewed = repo.heartbeat_ticket(
            conn,
            code,
            minutes=args.minutes,
            min_interval=0,
        )
        ticket = repo.get_ticket(conn, code)
    if not renewed:
        print(f"{code} no está en progreso o pertenece a otra sesión", file=sys.stderr)
        return 1
    print(f"{ticket['code']} lease hasta {ticket['lease_until']}")
    return 0
