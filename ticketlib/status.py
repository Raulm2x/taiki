"""Estado en vivo para agentes en paralelo: sesiones, claims y worktrees."""

from __future__ import annotations

import json
from datetime import datetime, timedelta
from typing import Any

from . import audit, db, servers, sessions as sessions_lib, workspace
from .domain import DEPENDENCY_DONE_STATUSES
from .store.workflow import stale_claims

DEFAULT_WINDOW = 600


def _paths(raw: Any) -> list[str]:
    if not raw:
        return []
    if isinstance(raw, list):
        return [str(item) for item in raw]
    try:
        parsed = json.loads(raw)
    except (TypeError, json.JSONDecodeError):
        return [str(raw)]
    return [str(item) for item in parsed] if isinstance(parsed, list) else [str(parsed)]


def _queue(conn: Any) -> dict[str, int]:
    """Cola del orquestador: listo, en curso, verificando y bloqueado."""
    placeholders = ", ".join("?" for _ in DEPENDENCY_DONE_STATUSES)
    counts = {
        row["status"]: row["total"]
        for row in conn.execute(
            "SELECT status, COUNT(*) AS total FROM tickets "
            "WHERE archived = 0 GROUP BY status"
        )
    }
    ready = conn.execute(
        "SELECT COUNT(*) FROM tickets t WHERE t.archived = 0 "
        "AND t.status IN ('nuevo', 'pendiente') "
        "AND NOT EXISTS (SELECT 1 FROM tickets ch "
        "WHERE ch.parent_id = t.id AND ch.archived = 0) "
        "AND NOT EXISTS (SELECT 1 FROM links dl "
        "JOIN tickets dt ON dt.code = dl.ref AND dt.archived = 0 "
        "WHERE dl.ticket_id = t.id AND dl.kind = 'depende' "
        f"AND dt.status NOT IN ({placeholders}))",
        DEPENDENCY_DONE_STATUSES,
    ).fetchone()[0]
    return {
        "ready": ready,
        "in_progress": counts.get("en_progreso", 0),
        "verifying": counts.get("verificado", 0),
        "blocked": counts.get("bloqueado", 0),
        # Tanto completado como verificado esperan al usuario; verificando es
        # el subconjunto ya validado por el conductor.
        "pending_confirmation": counts.get("completado", 0)
        + counts.get("verificado", 0),
    }


def _intervals(
    conn: Any, days: int = 30
) -> list[tuple[datetime, datetime]]:
    """Tramos en progreso recientes, según el historial (ventana acotada)."""
    cutoff = (
        datetime.now().astimezone() - timedelta(days=max(1, days))
    ).isoformat(timespec="seconds")
    rows = conn.execute(
        "SELECT ticket_id, ts, from_status, to_status FROM events "
        "WHERE action = 'transicion' AND ts >= ? ORDER BY ts, id",
        (cutoff,),
    ).fetchall()
    starts: dict[Any, datetime] = {}
    intervals: list[tuple[datetime, datetime]] = []
    for row in rows:
        try:
            moment = datetime.fromisoformat(row["ts"])
        except (TypeError, ValueError):
            continue
        if row["to_status"] == "en_progreso" and row["from_status"] != "en_progreso":
            starts[row["ticket_id"]] = moment
        elif (
            row["from_status"] == "en_progreso"
            and row["to_status"] != "en_progreso"
            and row["ticket_id"] in starts
        ):
            intervals.append((starts.pop(row["ticket_id"]), moment))
    now = datetime.now().astimezone()
    for start in starts.values():
        intervals.append((start, now))
    return intervals


def _max_overlap(intervals: list[tuple[datetime, datetime]]) -> int:
    """Máximo de intervalos solapados con una sweep line O(n log n)."""
    edges: list[tuple[datetime, int]] = []
    for start, end in intervals:
        if end <= start:
            continue
        edges.append((start, 1))
        edges.append((end, -1))
    edges.sort(key=lambda item: (item[0], item[1]))
    current = 0
    maximum = 0
    for _, delta in edges:
        current += delta
        if current > maximum:
            maximum = current
    return maximum


def _metrics(conn: Any, days: int = 30) -> dict[str, Any]:
    """Lead time, intentos, fallos de verificación y paralelismo observado."""
    cutoff = (
        datetime.now().astimezone() - timedelta(days=max(1, days))
    ).isoformat(timespec="seconds")
    row = conn.execute(
        "SELECT COUNT(*) AS total, AVG(iterations) AS attempts, "
        "AVG((julianday(completed_at) - julianday(started_at)) * 24.0) AS lead "
        "FROM tickets WHERE archived = 0 AND completed_at IS NOT NULL "
        "AND started_at IS NOT NULL AND completed_at >= ?",
        (cutoff,),
    ).fetchone()
    failures = conn.execute(
        "SELECT COUNT(*) FROM events WHERE from_status = 'verificado' "
        "AND to_status = 'en_progreso' AND ts >= ?",
        (cutoff,),
    ).fetchone()[0]
    max_parallel = _max_overlap(_intervals(conn, days))
    return {
        "closed": row["total"] or 0,
        "lead_time_hours": (
            round(row["lead"], 2) if row["lead"] is not None else None
        ),
        "avg_attempts": (
            round(row["attempts"], 2) if row["attempts"] is not None else None
        ),
        "verification_failures": failures,
        "max_parallel_observed": max_parallel,
        "cost": None,
        "tokens": None,
    }


def _recent(value: str | None, window: int) -> bool:
    if not value:
        return False
    try:
        moment = datetime.fromisoformat(value)
    except ValueError:
        return False
    return datetime.now().astimezone() - moment <= timedelta(seconds=window)


def snapshot(
    window: int = DEFAULT_WINDOW,
    *,
    with_servers: bool = True,
    conn: Any = None,
    metrics_days: int = 30,
) -> dict[str, Any]:
    """Foto del proyecto: tickets activos, dueños, leases, worktrees y MCP."""
    info = workspace.resolve()
    own = conn is None
    if own:
        conn = db.connect()
    try:
        rows = conn.execute(
            "SELECT code, title, status, priority, assigned_to, owner_session, "
            "heartbeat_at, lease_until, paths, updated_at FROM tickets "
            "WHERE archived = 0 AND status = 'en_progreso' ORDER BY updated_at DESC"
        ).fetchall()
        links = conn.execute(
            "SELECT t.code AS code, l.kind AS kind, l.ref AS ref FROM links l "
            "JOIN tickets t ON t.id = l.ticket_id "
            "WHERE l.kind IN ('worktree', 'rama') ORDER BY l.id"
        ).fetchall()
        stale = {item["code"] for item in stale_claims(conn)}
        overlaps = audit.overlaps(conn)
        queue = _queue(conn)
        metrics = _metrics(conn, metrics_days)
        activity = sessions_lib.ticket_activity(
            conn, [row["code"] for row in rows]
        )
        worktree_count = conn.execute(
            "SELECT COUNT(*) FROM links WHERE kind = 'worktree'"
        ).fetchone()[0]
    finally:
        if own:
            conn.close()

    worktrees: dict[str, dict[str, str]] = {}
    for link in links:
        worktrees.setdefault(link["code"], {})[link["kind"]] = link["ref"]

    sessions = []
    for row in rows:
        code = row["code"]
        related = worktrees.get(code, {})
        sessions.append(
            {
                "code": code,
                "title": row["title"],
                "priority": row["priority"],
                "agent": row["assigned_to"],
                "session": row["owner_session"],
                "heartbeat_at": row["heartbeat_at"],
                "lease_until": row["lease_until"],
                "recent": _recent(row["heartbeat_at"], window),
                "stale": code in stale,
                "paths": _paths(row["paths"]),
                "directory": activity.get(code, {}).get("directory"),
                "worktree": related.get("worktree"),
                "branch": related.get("rama"),
            }
        )

    registry = None
    fleet: list[dict[str, Any]] = []
    if with_servers:
        try:
            registry = db.connect(workspace.registry_path())
            fleet = servers.inventory(registry)
        except Exception:  # noqa: BLE001 - el inventario es informativo
            fleet = []
        finally:
            if registry is not None:
                registry.close()

    return {
        "project": info["project"],
        "db": str(info["db"]) if info["db"] else None,
        "workspace": str(info["workspace"]) if info["workspace"] else None,
        "window": window,
        "sessions": sessions,
        "queue": queue,
        "metrics": metrics,
        "worktrees": {"count": worktree_count},
        "stale": sorted(stale),
        "overlaps": overlaps,
        "busy": any(item["recent"] and not item["stale"] for item in sessions),
        "servers": fleet,
    }


def lines(data: dict[str, Any]) -> list[str]:
    """Representación en texto para CLI y modal de la TUI."""
    out = [
        f"Proyecto: {data.get('project') or '-'}",
        f"DB: {data.get('db') or '-'}",
        "",
    ]
    queue = data.get("queue") or {}
    out.append("Cola del orquestador")
    out.append(
        f"  listos {queue.get('ready', 0)} · "
        f"en curso {queue.get('in_progress', 0)} · "
        f"verificando {queue.get('verifying', 0)} · "
        f"bloqueados {queue.get('blocked', 0)} · "
        f"por confirmar {queue.get('pending_confirmation', 0)} "
        f"(incl. {queue.get('verifying', 0)} verificando)"
    )
    out.append(f"  worktrees {(data.get('worktrees') or {}).get('count', 0)}")
    metrics = data.get("metrics") or {}
    if metrics:
        lead = metrics.get("lead_time_hours")
        attempts = metrics.get("avg_attempts")
        out.append("Métricas")
        out.append(
            f"  lead time {lead if lead is not None else '-'} h · "
            f"intentos {attempts if attempts is not None else '-'} · "
            f"fallos de verificación {metrics.get('verification_failures', 0)} · "
            f"paralelismo máx {metrics.get('max_parallel_observed', 0)}"
        )
        cost = metrics.get("cost")
        out.append(
            "  coste/tokens: "
            + ("no disponible" if cost is None else str(cost))
        )
    out.append("")
    sessions = data.get("sessions") or []
    out.append(f"Sesiones activas ({len(sessions)})")
    if not sessions:
        out.append("  ninguna")
    for item in sessions:
        estado = "caducado" if item["stale"] else (
            "activa" if item["recent"] else "sin latido"
        )
        extra = item.get("branch") or item.get("worktree") or "-"
        out.append(
            f"  {item['code']}  {item['agent'] or '?'}  "
            f"{item['session'] or 'sin sesión'}  [{estado}]  {extra}"
        )
        if item.get("paths"):
            out.append(f"    rutas: {', '.join(item['paths'])}")
        if item.get("directory"):
            out.append(f"    dir: {item['directory']}")
        if item.get("worktree"):
            out.append(f"    worktree: {item['worktree']}")
    out.append("")
    overlaps = data.get("overlaps") or []
    out.append(f"Solapamientos ({len(overlaps)})")
    for item in overlaps:
        out.append(f"  {item['a']} ~ {item['b']}: {', '.join(item['patterns'])}")
    if not overlaps:
        out.append("  ninguno")
    fleet = data.get("servers") or []
    if fleet:
        alive = [item for item in fleet if item.get("state") == "al día"]
        out.append("")
        out.append(f"MCP: {len(alive)}/{len(fleet)} al día")
    return out


def session_lines(data: dict[str, Any]) -> list[str]:
    """Solo la tabla de sesiones (para el modal de la TUI)."""
    return lines(data)
