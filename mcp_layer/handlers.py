"""Ejecución de herramientas, recursos y prompts MCP."""

from __future__ import annotations

import os
import sqlite3
from dataclasses import replace
from pathlib import Path
from typing import Any, Callable
from urllib.parse import unquote

from ticketlib import (
    audit as audit_lib,
    db,
    domain,
    githook,
    presentation,
    render,
    repo,
    runtime,
    scratch as scratch_lib,
    servers as servers_lib,
    status as status_lib,
    workspace,
    worktree as worktree_lib,
)
from ticketlib.guard.context import find_active, session_chain
from ticketlib.util import normalize_agent, resolve_scope

from . import schemas, validation

ToolHandler = Callable[
    [sqlite3.Connection, dict[str, Any], str | None, str],
    dict[str, Any],
]


def resolve_session(
    meta: Any = None,
    args: dict[str, Any] | None = None,
) -> str | None:
    """Resuelve el identificador de sesión desde metadatos MCP, argumentos o entorno.

    Orden de precedencia:
    1. Metadatos de la petición MCP (meta / _meta): 'sessionId', 'session_id', 'conversationId', 'conversation_id', 'ai.opencode/sessionID'.
    2. Argumentos directos de la herramienta: 'session_id', 'sessionId', 'conversation_id', 'conversationId'.
    3. Variables de entorno del proceso: 'ANTIGRAVITY_CONVERSATION_ID', 'AGY_SESSION_ID', 'SESSION_ID', 'OPENCODE_SESSION_ID'.
    """
    # 1. Metadatos MCP
    if isinstance(meta, dict):
        for key in (
            "sessionId",
            "session_id",
            "conversationId",
            "conversation_id",
            "ai.opencode/sessionID",
        ):
            val = meta.get(key)
            if val is not None:
                cleaned = str(val).strip()
                if cleaned:
                    return cleaned

    # 2. Argumentos de la herramienta
    if isinstance(args, dict):
        for key in ("session_id", "sessionId", "conversation_id", "conversationId"):
            val = args.get(key)
            if val is not None:
                cleaned = str(val).strip()
                if cleaned:
                    return cleaned

    # 3. Variables de entorno del proceso (Antigravity CLI / OpenCode / runtime)
    for env_var in (
        "ANTIGRAVITY_CONVERSATION_ID",
        "AGY_SESSION_ID",
        "SESSION_ID",
        "OPENCODE_SESSION_ID",
    ):
        val = os.environ.get(env_var)
        if val is not None:
            cleaned = val.strip()
            if cleaned:
                return cleaned

    return None


def _session(meta: Any = None, args: dict[str, Any] | None = None) -> str | None:
    """Alias retrocompatible para resolve_session."""
    return resolve_session(meta=meta, args=args)


_CLIENT_AGENT = "agente"


def set_client_info(info: Any = None) -> str:
    """Fija el agente por defecto a partir de ``clientInfo`` del handshake."""
    global _CLIENT_AGENT
    name = ""
    if isinstance(info, dict):
        name = str(info.get("name") or "").strip()
    _CLIENT_AGENT = normalize_agent(name) or "agente"
    return _CLIENT_AGENT


def _actor(args: dict[str, Any]) -> str:
    """Nombre del agente: argumento explícito o cliente MCP conectado."""
    return normalize_agent(str(args.get("agent") or _CLIENT_AGENT))


# Campos que un agente necesita para elegir trabajo sin arrastrar el cuerpo
# completo de cada ticket. ``get`` sigue devolviendo el detalle.
DETAIL_SECTIONS = (
    "events",
    "comments",
    "links",
    "subtasks",
    "commits",
    "committed",
)
PREVIEW_CHARS = 240
IDEMPOTENCY_PREFIX = "idempotency:"
RECEIPT_FIELDS = (
    "code",
    "title",
    "status",
    "priority",
    "kind",
    "project",
    "assigned_to",
    "owner_session",
    "parent_id",
    "parent_code",
    "paths",
    "tags",
    "progress",
    "lease_until",
    "updated_at",
)


def _receipt(
    ticket: dict[str, Any],
    *,
    detail: bool = False,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Resumen de una mutación: evita devolver todo el historial."""
    if detail:
        return ticket
    data: dict[str, Any] = {}
    for key in RECEIPT_FIELDS:
        if key not in ticket:
            continue
        value = ticket[key]
        if value is None or value == [] or value == {} or value == "":
            continue
        if key == "progress" and not (value or {}).get("total"):
            continue
        data[key] = value
    if extra:
        data.update(
            {
                key: value
                for key, value in extra.items()
                if value is not None and value != [] and value != {}
            }
        )
    return data


def _project_root() -> Path:
    """Raíz del proyecto de la DB activa, no la del cwd del servidor."""
    info = workspace.resolve()
    if info.get("workspace"):
        return Path(info["workspace"]).parent
    return workspace.project_root()


def _summary(ticket: dict[str, Any], *, preview: bool = False) -> dict[str, Any]:
    data = presentation.summary(ticket)
    if preview:
        body = str(ticket.get("body") or "").strip()
        data["body_preview"] = (
            body[:PREVIEW_CHARS] + ("…" if len(body) > PREVIEW_CHARS else "")
        )
    return data


def _project_rows(
    rows: list[dict[str, Any]],
    *,
    detail: bool,
    fields: Any = None,
) -> list[dict[str, Any]]:
    if fields:
        wanted = [str(name) for name in fields if str(name).strip()]
        return [
            {key: row[key] for key in wanted if key in row}
            for row in rows
        ]
    if detail:
        return rows
    return [_summary(row) for row in rows]


# Los límites de secciones se aplican en SQL dentro de get_ticket.

# ---------------------------------------------------------------------------
# Manejadores Individuales de Herramientas (11 herramientas MCP)
# ---------------------------------------------------------------------------


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
            "subtasks": [item["code"] for item in ticket.get("subtasks", [])]
        },
    )


def _handle_list(
    conn: sqlite3.Connection,
    args: dict[str, Any],
    session: str | None = None,
    actor: str = "agente",
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
    return {
        "count": len(rows),
        "total": total,
        "offset": offset,
        "limit": limit,
        "order": order,
        "detail": bool(args.get("detail", False)),
        "hidden_children": hidden,
        "tickets": _project_rows(
            rows,
            detail=bool(args.get("detail", False)),
            fields=args.get("fields"),
        ),
    }


def _handle_get(
    conn: sqlite3.Connection,
    args: dict[str, Any],
    session: str | None = None,
    actor: str = "agente",
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
    if args.get("worktree"):
        ticket = _attach_worktree(conn, ticket)
    return _receipt(
        ticket,
        detail=bool(args.get("detail", False)),
        extra={
            "worktree": ticket.get("worktree"),
            "branch": ticket.get("branch"),
        },
    )


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
    return _receipt(
        ticket,
        detail=bool(args.get("detail", False)),
        extra={"commit": args.get("commit")},
    )


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
    args: dict[str, Any],
    session: str | None = None,
    actor: str = "agente",
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
    session: str | None = None,
    actor: str = "agente",
) -> dict[str, Any]:
    ticket = repo.next_ticket(conn, project=args.get("project"))
    if ticket is None:
        return {"ticket": None}
    if args.get("detail"):
        return {"ticket": ticket}
    return {"ticket": _summary(ticket, preview=True)}


def _handle_status(
    conn: sqlite3.Connection,
    args: dict[str, Any],
    session: str | None = None,
    actor: str = "agente",
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
    args: dict[str, Any],
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
    conn: sqlite3.Connection,
    args: dict[str, Any],
    session: str | None = None,
    actor: str = "agente",
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


def _handle_health(
    conn: sqlite3.Connection,
    args: dict[str, Any],
    session: str | None = None,
    actor: str = "agente",
) -> dict[str, Any]:
    root = _project_root()
    try:
        days = max(1, min(int(args.get("days", 2)), 365))
    except (TypeError, ValueError):
        days = 2
    stale = [item["code"] for item in repo.stale_claims(conn)]
    overlaps = audit_lib.overlaps(conn)
    unresolved = audit_lib.unresolved(conn)
    dirty_files = audit_lib.dirty(root)
    data: dict[str, Any] = {
        "ok": not stale and not overlaps and not unresolved and not dirty_files,
        "mcp": runtime.status(conn),
        "hooks": githook.hooks_installed(root),
        "stale_claims": stale,
        "overlaps": overlaps[:20],
        "unresolved": unresolved[:20],
        "dirty": dirty_files[:20],
        "gaps": audit_lib.code_gaps(conn)[:20],
        "stalled": audit_lib.stalled(conn, days=days)[:20],
        "worktrees": audit_lib.worktrees(conn, root)[:20],
    }
    if args.get("servers", True):
        registry = db.connect(workspace.registry_path())
        try:
            data["servers"] = servers_lib.summary(
                servers_lib.inventory(registry)
            )
        finally:
            registry.close()
    return data


# ---------------------------------------------------------------------------
# Tabla de Despacho (Registry)
# ---------------------------------------------------------------------------

TOOL_DISPATCH: dict[str, ToolHandler] = {
    spec.name: globals()[f"_handle_{spec.name}"]
    for spec in schemas.TOOL_SPECS
}
MUTATING_TOOLS = frozenset(
    spec.name for spec in schemas.TOOL_SPECS if not spec.read_only
)


# ---------------------------------------------------------------------------
# Punto de Entrada Principal para Despacho de Herramientas
# ---------------------------------------------------------------------------


def call_tool(
    name: str, args: dict[str, Any] | None = None, meta: Any = None
) -> dict[str, Any]:
    """Despacha la ejecución de una herramienta MCP utilizando TOOL_DISPATCH."""
    args = args or {}
    validation.validate_arguments(name, args)
    schema = validation.tool_schema(name)
    base = schema["name"] if schema else name
    if base in MUTATING_TOOLS and runtime.is_stale():
        raise domain.StaleServerError(
            "El MCP está desactualizado: no se ejecuta la escritura para no "
            "dejar datos inconsistentes. Recarga el servidor MCP (el plugin lo "
            "hace al quedar la sesión idle) o reinicia OpenCode."
        )

    handler = TOOL_DISPATCH.get(name)
    if not handler:
        for prefix in ("taiki_", "tickets_"):
            if name.startswith(prefix):
                handler = TOOL_DISPATCH.get(name.removeprefix(prefix))
                if handler:
                    break

    if not handler:
        raise domain.NotFoundError(f"Herramienta desconocida: {name}")

    conn = db.connect()
    try:
        session = resolve_session(meta=meta, args=args)
        actor = _actor(args)
        return handler(conn, args, session, actor)
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Recursos y Prompts MCP (Preservados)
# ---------------------------------------------------------------------------


def read_resource(uri: str) -> dict[str, Any]:
    # Acepta el esquema nuevo y el histórico `tickets://` como alias.
    if uri.startswith("tickets://"):
        uri = "taiki://" + uri.removeprefix("tickets://")
    conn = db.connect()
    try:
        if uri == "taiki://inbox":
            statuses = list(domain.OPEN_STATUSES)
            rows = repo.list_tickets(conn, statuses=statuses, limit=20)
            total = repo.count_tickets(conn, statuses=statuses)
            data: Any = {
                "count": len(rows),
                "total": total,
                "truncated": total > len(rows),
                "tickets": [_summary(row) for row in rows],
            }
        elif uri == "taiki://stats":
            data = repo.stats(conn)
        elif uri.startswith("taiki://ticket/"):
            code = unquote(uri.removeprefix("taiki://ticket/"))
            data = repo.get_ticket(conn, code, history_limit=50)
        else:
            raise domain.NotFoundError(f"Recurso desconocido: {uri}")
    finally:
        conn.close()

    return {
        "contents": [
            {
                "uri": uri,
                "mimeType": "application/json",
                "text": render.to_json(data, compact=True),
            }
        ]
    }


def get_prompt(name: str, args: dict[str, Any] | None = None) -> dict[str, Any]:
    args = args or {}
    conn = db.connect()
    try:
        if name == "next":
            ticket = repo.next_ticket(conn, project=args.get("project"))
            text = _next_prompt(ticket)
            description = "Siguiente ticket pendiente"
        elif name == "review":
            rows = repo.list_tickets(
                conn, statuses=["completado", "verificado"], limit=20
            )
            text = _review_prompt(rows)
            description = "Tickets completados o verificados pendientes de confirmación"
        else:
            raise domain.NotFoundError(f"Prompt desconocido: {name}")
    finally:
        conn.close()

    return {
        "description": description,
        "messages": [{"role": "user", "content": {"type": "text", "text": text}}],
    }


def _next_prompt(ticket: dict[str, Any] | None) -> str:
    if ticket is None:
        return (
            "No hay tickets pendientes ni nuevos. Si vas a hacer trabajo que no está "
            "registrado, créalo antes con create."
        )
    body = ticket.get("body") or "(sin descripción)"
    return f"""\
Trabaja este ticket:

{ticket['code']} — {ticket['title']}
Estado: {ticket['status']} | Prioridad: {ticket['priority']} | Tipo: {ticket['kind']}
Proyecto: {ticket.get('project') or '-'}

{body}

Pasos:
1. Reclámalo con claim(code="{ticket['code']}", agent="<tu nombre>").
2. Inspecciona el código y aplica el cambio mínimo.
3. Verifica el resultado.
4. Ciérralo con complete(code="{ticket['code']}", resolution="...", commit="...").
No lo confirmes: esa transición la hace el usuario con el CLI.
"""


def _review_prompt(rows: list[dict[str, Any]]) -> str:
    if not rows:
        return "No hay tickets completados o verificados pendientes de confirmación."
    lines = [
        "Tickets completados o verificados pendientes de confirmación del usuario:",
        "",
    ]
    for ticket in rows:
        lines.append(
            f"- {ticket['code']} ({ticket['priority']}): {ticket['title']}"
        )
    lines += [
        "",
        "Pide al usuario que revise y ejecute `taiki confirm <código>` para cada uno.",
    ]
    return "\n".join(lines)


__all__ = [
    "TOOL_DISPATCH",
    "call_tool",
    "get_prompt",
    "read_resource",
    "resolve_session",
    "set_client_info",
]
