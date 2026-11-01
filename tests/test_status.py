"""Estado en vivo de sesiones, claims y worktrees."""

from __future__ import annotations

import argparse
import contextlib
import io
from datetime import datetime, timedelta

from commands.status import cmd_status
from ticketlib import repo, status
from tests.support import Harness


def _past(minutes: int = 5) -> str:
    return (
        datetime.now().astimezone() - timedelta(minutes=minutes)
    ).isoformat(timespec="seconds")


def run(h: Harness) -> None:
    conn = h.conn
    first = repo.create_ticket(conn, title="Sesión A", project="taiki")
    second = repo.create_ticket(conn, title="Sesión B", project="taiki")
    repo.claim_ticket(
        conn, first["code"], agent="opencode", session_id="ses_a"
    )
    repo.claim_ticket(
        conn, second["code"], agent="opencode", session_id="ses_b"
    )
    repo.link_ticket(conn, first["code"], "worktree", "/tmp/wt-a")
    repo.link_ticket(conn, first["code"], "rama", "agent/T-0001-a")

    data = status.snapshot(with_servers=False)
    entries = {item["code"]: item for item in data["sessions"]}
    h.eq(len(entries), 2, "status lista las dos sesiones activas")
    h.ok(data["busy"], "status marca el proyecto como ocupado")
    h.eq(entries[first["code"]]["session"], "ses_a", "status expone la sesión dueña")
    h.eq(
        entries[first["code"]]["worktree"],
        "/tmp/wt-a",
        "status enlaza el worktree del ticket",
    )
    h.eq(
        entries[first["code"]]["branch"],
        "agent/T-0001-a",
        "status enlaza la rama del ticket",
    )
    h.eq(data["stale"], [], "sin leases vencidos no hay stale")

    conn.execute(
        "UPDATE tickets SET heartbeat_at = ?, lease_until = ? WHERE code = ?",
        (_past(), _past(), first["code"]),
    )
    conn.commit()
    data = status.snapshot(with_servers=False)
    h.ok(
        first["code"] in data["stale"] and data["busy"],
        "un lease vencido se marca pero la otra sesión mantiene busy",
    )

    conn.execute(
        "UPDATE tickets SET heartbeat_at = ?, lease_until = ? WHERE code = ?",
        (_past(), _past(), second["code"]),
    )
    conn.commit()
    data = status.snapshot(with_servers=False)
    h.ok(
        set(data["stale"]) == {first["code"], second["code"]}
        and not data["busy"],
        "sin latidos recientes el proyecto deja de estar ocupado",
    )

    text = "\n".join(status.lines(data))
    h.ok(
        first["code"] in text and "Solapamientos" in text,
        "lines resume sesiones y solapamientos",
    )
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        code = cmd_status(
            argparse.Namespace(
                window=600, json=False, sessions=True, no_servers=True
            )
        )
    h.ok(
        code == 0 and first["code"] in out.getvalue(),
        "taiki status --sessions responde por CLI",
    )
    from tui import view

    h.ok(
        hasattr(view, "status_modal")
        and any("Estado de sesiones" in line for line in view.HELP_LINES),
        "la TUI expone el modal de sesiones y su atajo",
    )
