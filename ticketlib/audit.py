"""Auditoría del trabajo: commits sin ticket y tickets estancados."""

from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from . import db, githook, runtime, servers
from .domain import TicketError
from .store.mutations import renumber_plan
from .store.relations import TICKET_CODE_RE


def _cutoff(days: int) -> str:
    moment = datetime.now().astimezone() - timedelta(days=days)
    return moment.isoformat(timespec="seconds")


def commits_without_ticket(
    repo: str | Path = ".", days: int = 7, limit: int = 100
) -> list[dict[str, str]]:
    """Commits recientes cuyo asunto no cita ningún T-XXXX."""
    try:
        output = githook.git(
            "log",
            f"--since={days} days ago",
            f"--max-count={limit}",
            "--date=short",
            "--format=%h%x09%ad%x09%s",
            cwd=Path(repo).expanduser(),
        )
    except TicketError:
        return []

    rows: list[dict[str, str]] = []
    for line in output.splitlines():
        parts = line.split("\t", 2)
        if len(parts) < 3:
            continue
        commit, date, subject = parts
        if not TICKET_CODE_RE.search(subject):
            rows.append({"commit": commit, "date": date, "subject": subject})
    return rows


def stalled(conn: Any, days: int = 2) -> list[dict[str, Any]]:
    """Tickets en progreso sin actividad reciente."""
    rows = conn.execute(
        "SELECT t.code, t.title, t.assigned_to, MAX(e.ts) AS last_ts "
        "FROM tickets t LEFT JOIN events e ON e.ticket_id = t.id "
        "WHERE t.archived = 0 AND t.status = 'en_progreso' "
        "AND NOT EXISTS (SELECT 1 FROM tags st WHERE st.ticket_id = t.id "
        "AND st.tag = '_scratch') "
        "GROUP BY t.id HAVING last_ts IS NULL OR last_ts < ? "
        "ORDER BY last_ts",
        (_cutoff(days),),
    ).fetchall()
    return [dict(row) for row in rows]


def code_gaps(conn: Any) -> list[dict[str, str]]:
    """Números de ticket que no existen, con su explicación si se conoce."""
    next_number = int(db.meta_get(conn, "next_ticket_number", "1") or "1")
    roots = {
        row["code"]
        for row in conn.execute(
            "SELECT code FROM tickets WHERE code NOT LIKE '%.%'"
        )
    }
    renames: dict[str, str] = {}
    for row in conn.execute(
        "SELECT note FROM events WHERE action = 'renumerar'"
    ):
        note = row["note"] or ""
        if "→" in note:
            old, new = note.split("→", 1)
            renames[old.strip()] = new.strip()

    gaps: list[dict[str, str]] = []
    for number in range(1, next_number):
        code = f"T-{number:04d}"
        if code in roots:
            continue
        new = renames.get(code)
        gaps.append(
            {
                "code": code,
                "reason": f"renumerado a {new}" if new else "sin rastro",
            }
        )
    return gaps


def unresolved(conn: Any) -> list[dict[str, Any]]:
    """Tickets cerrados sin resolución escrita."""
    rows = conn.execute(
        "SELECT code, title, status FROM tickets "
        "WHERE status IN ('completado', 'confirmado') "
        "AND (resolution IS NULL OR TRIM(resolution) = '') "
        "ORDER BY code DESC"
    ).fetchall()
    return [dict(row) for row in rows]


def uncommitted(conn: Any) -> list[dict[str, Any]]:
    """Confirmados o completados sin ningún commit enlazado."""
    rows = conn.execute(
        "SELECT t.code, t.title, t.status FROM tickets t "
        "WHERE t.archived = 0 AND t.status IN ('completado', 'confirmado') "
        "AND NOT EXISTS (SELECT 1 FROM links l "
        "WHERE l.ticket_id = t.id AND l.kind = 'commit') "
        "ORDER BY t.code DESC"
    ).fetchall()
    return [dict(row) for row in rows]


def audit(
    conn: Any,
    repo: str | Path = ".",
    days: int = 7,
    limit: int = 100,
    stalled_days: int = 2,
) -> dict[str, Any]:
    """Resumen completo para ``taiki audit``."""
    fleet = servers.inventory(conn)
    return {
        "repo": str(Path(repo).expanduser().resolve()),
        "days": days,
        "commits": commits_without_ticket(repo, days=days, limit=limit),
        "stalled": stalled(conn, days=stalled_days),
        "unresolved": unresolved(conn),
        "uncommitted": uncommitted(conn),
        "misnumbered": renumber_plan(conn),
        "gaps": code_gaps(conn),
        "mcp": runtime.status(conn),
        "servers": {"summary": servers.summary(fleet), "items": fleet},
        "hooks": githook.hooks_installed(repo),
    }
