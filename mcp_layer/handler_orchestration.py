"""Manejadores MCP de orquestación, worktrees y estado."""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

from ticketlib import (
    agents as agents_lib,
    domain,
    orchestrator as orchestrator_lib,
    repo,
    scratch as scratch_lib,
    status as status_lib,
    workspace,
    worktree as worktree_lib,
)
from ticketlib.guard.context import find_active, session_chain
from ticketlib.memory.advice import routing_advice

from .handler_common import _project_root, _summary
from .handler_tickets import _attach_worktree


def _handle_agents(
    conn: sqlite3.Connection,
    args: dict[str, Any],
    _session: str | None = None,
    _actor: str = "agente",
) -> dict[str, Any]:
    info = workspace.resolve()
    config = workspace.load_config(info["workspace"]) if info["workspace"] else {}
    table = agents_lib.registry(config)
    data: dict[str, Any] = {
        "agents": [table[name] for name in sorted(table)],
        "fallback": agents_lib.FALLBACK_AGENT,
        "concurrency": agents_lib.concurrency_budget(config),
    }
    kind = args.get("kind")
    if kind:
        data["recommended"] = agents_lib.recommend(kind, config)
        data["routing"] = agents_lib.assign(kind, config)
        data["advice"] = routing_advice(conn, kind, [], config)
    else:
        data["advice"] = None
    return data


def _handle_status(
    conn: sqlite3.Connection,
    args: dict[str, Any],
    _session: str | None = None,
    _actor: str = "agente",
) -> dict[str, Any]:
    try:
        window = max(60, min(int(args.get("window", 600)), 86400))
    except (TypeError, ValueError):
        window = 600
    return status_lib.snapshot(
        window=window,
        with_servers=bool(args.get("servers", False)),
        conn=conn,
    )


def _handle_whoami(
    conn: sqlite3.Connection,
    _args: dict[str, Any],
    session: str | None = None,
    actor: str = "agente",
) -> dict[str, Any]:
    info = workspace.resolve()
    config = workspace.load_config(info["workspace"]) if info["workspace"] else {}
    data: dict[str, Any] = {
        "agent": actor,
        "session": session,
        "project": info["project"],
        "db": str(info["db"]) if info["db"] else None,
        "guard": bool(config.get("guard")),
    }
    active = find_active(conn, session_chain(session), actor)
    if active is None:
        data["active"] = None
        return data
    summary = _summary(active)
    summary["lease_until"] = active.get("lease_until")
    found = worktree_lib.locate(_project_root(), active["code"])
    summary["worktree"] = str(found["path"]) if found else None
    data["active"] = summary
    return data


def _handle_checkpoint(
    conn: sqlite3.Connection,
    args: dict[str, Any],
    session: str | None = None,
    actor: str = "agente",
) -> dict[str, Any]:
    return orchestrator_lib.checkpoint(
        conn,
        args.get("code"),
        args.get("note") or "",
        pending=args.get("pending"),
        agent=actor,
        session_id=session,
    )


def _handle_retry(
    conn: sqlite3.Connection,
    args: dict[str, Any],
    session: str | None = None,
    actor: str = "agente",
) -> dict[str, Any]:
    return orchestrator_lib.retry(
        conn,
        args.get("code"),
        args.get("why") or "",
        agent=actor,
        session_id=session,
        limit=args.get("limit"),
    )


def _handle_reassign(
    conn: sqlite3.Connection,
    _args: dict[str, Any],
    session: str | None = None,
    actor: str = "agente",
) -> dict[str, Any]:
    return orchestrator_lib.reassign_stale(
        conn, agent=actor, session_id=session
    )


def _handle_schedule(
    conn: sqlite3.Connection,
    args: dict[str, Any],
    _session: str | None = None,
    _actor: str = "agente",
) -> dict[str, Any]:
    info = workspace.resolve()
    config = workspace.load_config(info["workspace"]) if info["workspace"] else {}
    plan = orchestrator_lib.parallel_plan(
        conn,
        project=args.get("project"),
        limit=args.get("limit"),
        config=config,
    )
    codes = [
        *plan["parallel"],
        *(item["code"] for item in plan["serial"]),
        *plan["deferred"],
    ]
    advice: dict[str, Any] = {}
    for code in dict.fromkeys(codes):
        ticket = repo.get_ticket(conn, code)
        advice[code] = routing_advice(
            conn, ticket.get("kind"), ticket.get("paths") or [], config
        )
    plan["advice"] = advice
    return plan


def _handle_heartbeat(
    conn: sqlite3.Connection,
    args: dict[str, Any],
    session: str | None = None,
    actor: str = "agente",
) -> dict[str, Any]:
    code = args.get("code")
    if not code:
        active = find_active(conn, session_chain(session), actor)
        if active is None:
            raise domain.ValidationError(
                "No hay ticket activo; indica code o reclama uno con claim."
            )
        code = active["code"]
    ticket = repo.get_ticket(conn, code)
    owner = ticket["owner_session"]
    if owner and not (
        session == owner or (session is None and owner == f"agent:{actor}")
    ):
        raise domain.PermissionDenied(
            f"{code} pertenece a la sesión {owner}; no puedes renovar su lease."
        )
    renewed = repo.heartbeat_ticket(
        conn,
        code,
        session,
        minutes=args.get("minutes"),
        min_interval=0,
    )
    if not renewed:
        raise domain.TransitionError(
            f"No se pudo renovar {code}: no está en progreso o la sesión no es la dueña."
        )
    updated = repo.get_ticket(conn, code)
    return {
        "code": code,
        "renewed": True,
        "heartbeat_at": updated["heartbeat_at"],
        "lease_until": updated["lease_until"],
    }


def _handle_worktree_create(
    conn: sqlite3.Connection,
    args: dict[str, Any],
    session: str | None = None,
    actor: str = "agente",
) -> dict[str, Any]:
    code = args.get("code")
    if not code:
        raise domain.ValidationError("El parámetro 'code' es obligatorio")
    ticket = repo.get_ticket(conn, code)
    if ticket["status"] != "en_progreso" and not args.get("force"):
        raise domain.ValidationError(
            f"{code} no está en progreso; reclámalo antes de crear el worktree."
        )
    owner = ticket["owner_session"]
    if owner and not args.get("force") and not (
        session == owner or (session is None and owner == f"agent:{actor}")
    ):
        raise domain.PermissionDenied(
            f"{code} está reclamado por la sesión {owner}."
        )
    updated = _attach_worktree(conn, ticket, title=args.get("title"))
    return {
        "code": code,
        "path": updated["worktree"],
        "branch": updated.get("branch"),
    }


def _handle_worktree_list(
    _conn: sqlite3.Connection,
    args: dict[str, Any],
    _session: str | None = None,
    _actor: str = "agente",
) -> dict[str, Any]:
    root = _project_root()
    items = worktree_lib.list_worktrees(root)
    code = args.get("code")
    if code:
        items = [
            item
            for item in items
            if str(item.get("branch") or "").startswith(f"agent/{code}")
            or Path(item.get("path", "")).name == code
        ]
    return {"count": len(items), "worktrees": items}


def _handle_promote(
    conn: sqlite3.Connection,
    args: dict[str, Any],
    session: str | None = None,
    actor: str = "agente",
) -> dict[str, Any]:
    code = args.get("code")
    if not code:
        raise domain.ValidationError("El parámetro 'code' es obligatorio")
    return scratch_lib.promote(
        conn,
        code,
        actor=actor,
        actor_type="agente",
        session_id=session,
    )


def _handle_scratch_drop(
    conn: sqlite3.Connection,
    args: dict[str, Any],
    session: str | None = None,
    actor: str = "agente",
) -> dict[str, Any]:
    code = args.get("code")
    if not code:
        raise domain.ValidationError("El parámetro 'code' es obligatorio")
    return scratch_lib.drop(
        conn,
        code,
        actor=actor,
        actor_type="agente",
        session_id=session,
    )
