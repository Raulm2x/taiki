"""Estado en vivo para agentes en paralelo: sesiones, claims y worktrees."""

from __future__ import annotations

import json
from datetime import datetime, timedelta
from typing import Any

from . import audit, db, servers, workspace
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
