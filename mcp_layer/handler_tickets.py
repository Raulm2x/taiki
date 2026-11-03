"""Manejadores MCP del dominio de tickets."""

from __future__ import annotations

import sqlite3
from dataclasses import replace
from typing import Any

from ticketlib import (
    db,
    domain,
    orchestrator as orchestrator_lib,
    repo,
    runtime,
    workspace,
    worktree as worktree_lib,
)
from ticketlib.util import resolve_scope

from . import memory as memory_handlers
from .handler_common import (
    DETAIL_SECTIONS,
    IDEMPOTENCY_PREFIX,
    _project_root,
    _project_rows,
    _receipt,
    _summary,
)


def _handle_create(
    conn: sqlite3.Connection,
    args: dict[str, Any],
    session: str | None = None,
    actor: str = "agente",
) -> dict[str, Any]:
    if args.get("parent_id") and runtime.is_stale():
        raise domain.StaleServerError(
            "El MCP está desactualizado: no se crea la tarea para no numerarla "
            "mal. Recarga el servidor MCP (el plugin lo hace al quedar la sesión "
            "idle) o reinicia OpenCode."
        )
    subtasks: list[str] = []
    for raw in args.get("subtasks") or []:
        title = str(raw or "").strip()
        if not title:
            raise domain.ValidationError(
                "Cada subtarea necesita un título no vacío"
            )
        subtasks.append(title)

    detail = bool(args.get("detail", False))
    key = str(args.get("idempotency_key") or "").strip()
    if key:
        previous = db.meta_get(conn, f"{IDEMPOTENCY_PREFIX}{key}")
        if previous:
            try:
                ticket = repo.get_ticket(conn, previous)
            except domain.NotFoundError:
                pass
            else:
                return _receipt(
                    ticket,
                    detail=detail,
                    extra={
                        "idempotent_replay": True,
                        "subtasks": [
                            item["code"] for item in ticket.get("subtasks", [])
                        ],
                    },
                )

    project, path = resolve_scope(args.get("project"), args.get("path"))
    config = workspace.load_config()
    defaults = config.get("defaults") or {}
    kind = args.get("kind") or defaults.get("kind") or "otro"
    priority = args.get("priority") or defaults.get("priority") or "media"
    isolation = args.get("isolation") or workspace.isolation_settings(config)["default"]
    assigned = actor if actor not in ("", "agente", "usuario") else None

    with db.tx(conn):
        ticket = repo.create_ticket(
            conn,
            title=args.get("title", ""),
            body=args.get("body", ""),
            kind=kind,
            priority=priority,
            project=project,
            path=path,
            tags=args.get("tags"),
            paths=args.get("paths"),
            isolation=isolation,
            resources=args.get("resources"),
            reversal=args.get("reversal"),
            parent_id=args.get("parent_id"),
            created_by=actor,
            actor_type="agente",
            session_id=session,
            assigned_to=assigned,
        )
        if key:
            db.meta_set(conn, f"{IDEMPOTENCY_PREFIX}{key}", ticket["code"])
        for title in subtasks:
            repo.create_ticket(
                conn,
                title=title,
                body="",
                kind=kind,
                priority=priority,
                project=ticket.get("project"),
                path=ticket.get("path"),
                paths=ticket.get("paths"),
                isolation=isolation,
                resources=args.get("resources"),
                parent_id=ticket["code"],
                created_by=actor,
                actor_type="agente",
                session_id=session,
                assigned_to=assigned,
            )
    ticket = repo.get_ticket(conn, ticket["code"])
    return _receipt(
        ticket,
        detail=detail,
        extra={
            "subtasks": [item["code"] for item in ticket.get("subtasks", [])],
            "isolation": ticket.get("isolation"),
            "resources": ticket.get("resources"),
            "reversal": ticket.get("reversal"),
        },
    )


def _handle_list(
    conn: sqlite3.Connection,
    args: dict[str, Any],
    _session: str | None = None,
    _actor: str = "agente",
) -> dict[str, Any]:
    status = args.get("status")
    statuses = args.get("statuses")
    include_closed = bool(args.get("include_closed", False))
    if not status and not statuses and not include_closed:
        statuses = list(domain.OPEN_STATUSES)
    base_filter = repo.TicketFilter(
        status=status,
        statuses=statuses,
        kind=args.get("kind"),
        priority=args.get("priority"),
        project=args.get("project"),
        assignee=args.get("assignee"),
        query=args.get("query"),
        include_archived=bool(args.get("include_archived", False)),
        tag=args.get("tag"),
        tags=args.get("tags"),
        ready_only=bool(args.get("ready", False)),
    )
    ticket_filter = base_filter
    if args.get("only_roots"):
        ticket_filter = replace(ticket_filter, roots_only=True)
    if args.get("leaves_only"):
        ticket_filter = replace(ticket_filter, leaves_only=True)
    if args.get("parent_id"):
        ticket_filter = replace(ticket_filter, parent_id=args["parent_id"])
    limit = args.get("limit", 20)
    offset = args.get("offset", 0)
    order = args.get("order", "code")
    fields = args.get("fields") or ()
    detail = bool(args.get("detail", False)) or bool(
        {"body", "resolution"} & {str(name) for name in fields}
    )
    rows = repo.list_tickets(
        conn,
        filters=ticket_filter,
        limit=limit,
        offset=offset,
        order=order,
        detail=detail,
    )
    total = repo.count_tickets(conn, filters=ticket_filter)
    hidden = 0
    if args.get("only_roots"):
        hidden = max(0, repo.count_tickets(conn, filters=base_filter) - total)
    projected = _project_rows(
        rows,
        detail=bool(args.get("detail", False)),
        fields=args.get("fields"),
    )
    if not detail and not fields:
        for row, item in zip(rows, projected):
            item["isolation"] = row.get("isolation")
    return {
        "count": len(rows),
        "total": total,
        "offset": offset,
        "limit": limit,
        "order": order,
        "detail": bool(args.get("detail", False)),
        "hidden_children": hidden,
        "scope_roots": workspace.scope_roots(),
        "tickets": projected,
    }


def _handle_get(
    conn: sqlite3.Connection,
    args: dict[str, Any],
    _session: str | None = None,
    _actor: str = "agente",
) -> dict[str, Any]:
    code = args.get("code")
    if not code:
        raise domain.ValidationError("El parámetro 'code' es obligatorio")
    # ``raw`` devuelve el registro íntegro: sin recortes ni proyecciones. Es la
    # vía para recuperar el note del evento de cierre o los campos completos de
    # los eventos sin depender de los límites por defecto.
    if args.get("raw", False):
        return repo.get_ticket(conn, code)
    ticket = repo.get_ticket(
        conn,
        code,
        history_limit=args.get("history_limit", 15),
        links_limit=args.get("links_limit", 20),
        subtasks_limit=args.get("subtasks_limit", 20),
        commits_limit=args.get("commits_limit", 20),
        fields=args.get("fields"),
    )
    include = args.get("include")
    if include is not None:
        keep = {str(name) for name in include}
        for section in DETAIL_SECTIONS:
            if section not in keep:
                ticket.pop(section, None)
                ticket.pop(f"{section}_total", None)
    if not args.get("events_detail", False):
        for event in ticket.get("events", []):
            for key in ("actor_type", "from_status", "to_status", "session_id"):
                event.pop(key, None)
    if not args.get("fields"):
        ticket["scope"] = workspace.scope_roots()
    return ticket


def _attach_worktree(
    conn: sqlite3.Connection,
    ticket: dict[str, Any],
    *,
    title: str | None = None,
) -> dict[str, Any]:
    """Crea (o reutiliza) el worktree del ticket y lo enlaza."""
    root = _project_root()
    found = worktree_lib.locate(root, ticket["code"])
    if found is None:
        created = worktree_lib.create(
            root, ticket["code"], title=title or ticket.get("title")
        )
        path, branch = str(created["path"]), created["branch"]
    else:
        path = str(found["path"])
        branch = found.get("branch")
    repo.link_ticket(conn, ticket["code"], "worktree", path)
    if branch:
        repo.link_ticket(conn, ticket["code"], "rama", branch)
    updated = repo.get_ticket(conn, ticket["code"])
    updated["worktree"] = path
    if branch:
        updated["branch"] = branch
    return updated


def _handle_claim(
    conn: sqlite3.Connection,
    args: dict[str, Any],
    session: str | None = None,
    actor: str = "agente",
) -> dict[str, Any]:
    code = args.get("code")
    if not code:
        raise domain.ValidationError("El parámetro 'code' es obligatorio")
    if args.get("worktree"):
        # none/scope trabajan en el árbol vivo: el worktree no aplica y se
        # rechaza antes de reclamar para no dejar el ticket a medio asignar.
        target = repo.get_ticket(conn, code)
        isolation = str(target.get("isolation") or domain.DEFAULT_ISOLATION)
        if isolation in orchestrator_lib.LIVE_ISOLATIONS:
            raise domain.ValidationError(
                f"{target['code']} usa aislamiento {isolation}: no crea "
                "worktree; reclama sin worktree y trabaja en el checkout live."
            )
    ticket = repo.claim_ticket(
        conn,
        code,
        agent=actor,
        session_id=session,
        steal=bool(args.get("steal")),
        force=bool(args.get("force")),
        anonymous_owner=True,
        lease_minutes=args.get("lease_minutes"),
    )
    isolation = str(ticket.get("isolation") or domain.DEFAULT_ISOLATION)
    if args.get("worktree"):
        ticket = _attach_worktree(conn, ticket)
    scope = workspace.scope_roots()
    ticket["scope"] = scope
    result = _receipt(
        ticket,
        detail=bool(args.get("detail", False)),
        extra={
            "worktree": ticket.get("worktree"),
            "branch": ticket.get("branch"),
            "isolation": isolation,
            "requires_worktree": isolation == "worktree",
            "resources": ticket.get("resources"),
            "scope": scope,
        },
    )
    result["hints"] = memory_handlers.hints(conn, ticket)
    return result


def _handle_update(
    conn: sqlite3.Connection,
    args: dict[str, Any],
    session: str | None = None,
    actor: str = "agente",
) -> dict[str, Any]:
    code = args.get("code")
    if not code:
        raise domain.ValidationError("El parámetro 'code' es obligatorio")
    fields = {
        key: args[key]
        for key in (
            "title",
            "body",
            "kind",
            "priority",
            "project",
            "path",
            "paths",
            "isolation",
            "resources",
            "reversal",
            "tags",
            "parent_id",
            "assigned_to",
        )
        if key in args
    }
    ticket = repo.update_ticket(
        conn,
        code,
        actor=actor,
        session_id=session,
        **fields,
    )
    extra = {"changed": sorted(fields)}
    extra.update({key: ticket.get(key) for key in fields})
    return _receipt(
        ticket,
        detail=bool(args.get("detail", False)),
        extra=extra,
    )


def _handle_transition(
    conn: sqlite3.Connection,
    args: dict[str, Any],
    session: str | None = None,
    actor: str = "agente",
) -> dict[str, Any]:
    code = args.get("code")
    if not code:
        raise domain.ValidationError("El parámetro 'code' es obligatorio")
    status = args.get("status")
    if not status:
        raise domain.ValidationError("El parámetro 'status' es obligatorio")
    ticket = repo.transition(
        conn,
        code,
        status,
        note=args.get("note"),
        actor=actor,
        actor_type="agente",
        session_id=session,
        resolution=args.get("resolution"),
        force=bool(args.get("force")),
    )
    return _receipt(
        ticket,
        detail=bool(args.get("detail", False)),
    )


def _handle_complete(
    conn: sqlite3.Connection,
    args: dict[str, Any],
    session: str | None = None,
    actor: str = "agente",
) -> dict[str, Any]:
    code = args.get("code")
    if not code:
        raise domain.ValidationError("El parámetro 'code' es obligatorio")
    resolution = args.get("resolution")
    if not resolution:
        raise domain.ValidationError("El parámetro 'resolution' es obligatorio")
    ticket = repo.complete_ticket(
        conn,
        code,
        resolution=resolution,
        commit=args.get("commit"),
        files=args.get("files"),
        actor=actor,
        actor_type="agente",
        session_id=session,
        force=bool(args.get("force")),
    )
    result = _receipt(
        ticket,
        detail=bool(args.get("detail", False)),
        extra={"commit": args.get("commit")},
    )
    nudge = memory_handlers.learning(ticket)
    if nudge:
        result["learning"] = nudge
    return result


def _handle_comment(
    conn: sqlite3.Connection,
    args: dict[str, Any],
    session: str | None = None,
    actor: str = "agente",
) -> dict[str, Any]:
    code = args.get("code")
    if not code:
        raise domain.ValidationError("El parámetro 'code' es obligatorio")
    body = args.get("body")
    if body is None:
        raise domain.ValidationError("El parámetro 'body' es obligatorio")
    ticket = repo.comment_ticket(
        conn,
        code,
        body=body,
        author=actor,
        actor_type="agente",
        session_id=session,
    )
    comments = ticket.get("comments") or []
    return _receipt(
        ticket,
        detail=bool(args.get("detail", False)),
        extra={
            "comment": comments[-1] if comments else None,
            "comments_count": len(comments),
        },
    )


def _handle_link(
    conn: sqlite3.Connection,
    args: dict[str, Any],
    session: str | None = None,
    actor: str = "agente",
) -> dict[str, Any]:
    code = args.get("code")
    if not code:
        raise domain.ValidationError("El parámetro 'code' es obligatorio")
    kind = args.get("kind")
    if not kind:
        raise domain.ValidationError("El parámetro 'kind' es obligatorio")
    ref = args.get("ref")
    if not ref:
        raise domain.ValidationError("El parámetro 'ref' es obligatorio")
    ticket = repo.link_ticket(
        conn,
        code,
        kind=kind,
        ref=ref,
        actor=actor,
        actor_type="agente",
        session_id=session,
    )
    links = ticket.get("links") or []
    return _receipt(
        ticket,
        detail=bool(args.get("detail", False)),
        extra={
            "link": links[-1] if links else None,
            "links_count": len(links),
        },
    )


def _handle_stats(
    conn: sqlite3.Connection,
    _args: dict[str, Any],
    _session: str | None = None,
    _actor: str = "agente",
) -> dict[str, Any]:
    data = repo.stats(conn)
    info = workspace.resolve()
    data["workspace"] = {
        "project": info["project"],
        "source": info["source"],
        "db": str(info["db"]),
    }
    return data


def _handle_next(
    conn: sqlite3.Connection,
    args: dict[str, Any],
    _session: str | None = None,
    _actor: str = "agente",
) -> dict[str, Any]:
    ticket = repo.next_ticket(
        conn,
        project=args.get("project"),
        ready=bool(args.get("ready", True)),
    )
    if ticket is None:
        return {"ticket": None}
    if args.get("detail"):
        return {"ticket": ticket}
    return {"ticket": _summary(ticket, preview=True)}
