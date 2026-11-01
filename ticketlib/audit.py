"""Auditoría del trabajo: commits sin ticket y tickets estancados."""

from __future__ import annotations

import json
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from . import commits, githook, runtime, servers
from .domain import SCRATCH_TAG, TicketError
from .store.mutations import renumber_plan
from .store.queries import hidden_codes, missing_codes
from .store.relations import TICKET_CODE_RE
from .store.workflow import stale_claims
from . import worktree as worktree_lib


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
        "AND st.tag = ?) "
        "GROUP BY t.id HAVING last_ts IS NULL OR last_ts < ? "
        "ORDER BY last_ts",
        (SCRATCH_TAG, _cutoff(days)),
    ).fetchall()
    return [dict(row) for row in rows]


def code_gaps(conn: Any) -> list[dict[str, str]]:
    """Números de ticket que nunca existieron, con su explicación si se conoce."""
    return missing_codes(conn)


def hidden(conn: Any) -> list[dict[str, str]]:
    """Códigos consumidos por tickets archivados o temporales (falsos huecos)."""
    return hidden_codes(conn)


def overlaps(conn: Any) -> list[dict[str, Any]]:
    """Pares de tickets en progreso con rutas potencialmente solapadas."""
    rows = conn.execute(
        "SELECT code, paths FROM tickets WHERE archived = 0 "
        "AND status = 'en_progreso' ORDER BY code"
    ).fetchall()
    tickets = []
    for row in rows:
        raw = row["paths"]
        try:
            paths = json.loads(raw) if raw else []
        except (TypeError, json.JSONDecodeError):
            paths = [raw] if raw else []
        if paths:
            tickets.append({"code": row["code"], "paths": paths})
    result: list[dict[str, Any]] = []
    for index, left in enumerate(tickets):
        for right in tickets[index + 1 :]:
            hits = commits.overlaps(left["paths"], right["paths"])
            if hits:
                result.append(
                    {"a": left["code"], "b": right["code"], "patterns": hits}
                )
    return result


def dirty(repo: str | Path) -> list[str]:
    """Ficheros sin stagear o sin seguimiento en el checkout."""
    try:
        return commits.unstaged(repo)
    except TicketError:
        return []


def worktrees(conn: Any, repo: str | Path) -> list[str]:
    """Avisos de worktrees registrados (sucios, ausentes o sin mergear)."""
    rows = conn.execute(
        "SELECT t.code AS code, l.ref AS ref FROM links l "
        "JOIN tickets t ON t.id = l.ticket_id WHERE l.kind = 'worktree'"
    ).fetchall()
    entries = [{"code": row["code"], "path": row["ref"]} for row in rows]
    try:
        return worktree_lib.warnings(Path(repo), entries)
    except TicketError:
        return []


def unresolved(conn: Any) -> list[dict[str, Any]]:
    """Tickets cerrados sin resolución escrita."""
    rows = conn.execute(
        "SELECT code, title, status FROM tickets "
        "WHERE status IN ('completado', 'verificado', 'confirmado') "
        "AND (resolution IS NULL OR TRIM(resolution) = '') "
        "ORDER BY code DESC"
    ).fetchall()
    return [dict(row) for row in rows]


def uncommitted(conn: Any) -> list[dict[str, Any]]:
    """Confirmados, completados o verificados sin ningún commit enlazado."""
    rows = conn.execute(
        "SELECT t.code, t.title, t.status FROM tickets t "
        "WHERE t.archived = 0 "
        "AND t.status IN ('completado', 'verificado', 'confirmado') "
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
        "stale_claims": stale_claims(conn),
        "unresolved": unresolved(conn),
        "uncommitted": uncommitted(conn),
        "misnumbered": renumber_plan(conn),
        "gaps": code_gaps(conn),
        "hidden": hidden(conn),
        "overlaps": overlaps(conn),
        "dirty": dirty(repo),
        "worktrees": worktrees(conn, repo),
        "mcp": runtime.status(conn),
        "servers": {"summary": servers.summary(fleet), "items": fleet},
        "hooks": githook.hooks_installed(repo),
    }
