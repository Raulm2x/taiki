"""Ejecución de herramientas, recursos y prompts MCP."""

from __future__ import annotations

import os
import sqlite3
from typing import Any, Callable
from urllib.parse import unquote

from ticketlib import db, domain, render, repo, runtime, workspace
from ticketlib.util import normalize_agent, resolve_scope

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


def _actor(args: dict[str, Any]) -> str:
    """Determina el nombre del agente ejecutor a partir de los argumentos."""
    return normalize_agent(str(args.get("agent") or "agente"))


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
        raise domain.ValidationError(
            "El MCP está desactualizado: no se crea la tarea para no numerarla "
            "mal. Recarga el servidor MCP (el plugin lo hace al quedar la sesión "
            "idle) o reinicia OpenCode."
        )
    project, path = resolve_scope(args.get("project"), args.get("path"))
    config = workspace.load_config()
    defaults = config.get("defaults") or {}
    kind = args.get("kind") or defaults.get("kind") or "otro"
    priority = args.get("priority") or defaults.get("priority") or "media"
    return repo.create_ticket(
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
        assigned_to=actor if actor not in ("", "agente", "usuario") else None,
    )


def _handle_list(
    conn: sqlite3.Connection,
    args: dict[str, Any],
    session: str | None = None,
    actor: str = "agente",
) -> dict[str, Any]:
    filters = {
        "status": args.get("status"),
        "statuses": args.get("statuses"),
        "kind": args.get("kind"),
        "priority": args.get("priority"),
        "project": args.get("project"),
        "assignee": args.get("assignee"),
        "query": args.get("query"),
        "include_archived": bool(args.get("include_archived", False)),
        "tag": args.get("tag"),
        "tags": args.get("tags"),
    }
    hierarchy: dict[str, Any] = {}
    if args.get("only_roots"):
        hierarchy["roots_only"] = True
    if args.get("parent_id"):
        hierarchy["parent_id"] = args["parent_id"]
    limit = args.get("limit", 50)
    offset = args.get("offset", 0)
    order = args.get("order", "code")
    rows = repo.list_tickets(
        conn,
        **filters,
        **hierarchy,
        limit=limit,
        offset=offset,
        order=order,
    )
    total = repo.count_tickets(conn, **filters, **hierarchy)
    hidden = 0
    if hierarchy.get("roots_only"):
        hidden = max(0, repo.count_tickets(conn, **filters) - total)
    return {
        "count": len(rows),
        "total": total,
        "offset": offset,
        "limit": limit,
        "order": order,
        "hidden_children": hidden,
        "tickets": rows,
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
    return repo.get_ticket(conn, code)


def _handle_claim(
    conn: sqlite3.Connection,
    args: dict[str, Any],
    session: str | None = None,
    actor: str = "agente",
) -> dict[str, Any]:
    code = args.get("code")
    if not code:
        raise domain.ValidationError("El parámetro 'code' es obligatorio")
    return repo.claim_ticket(
        conn,
        code,
        agent=actor,
        session_id=session,
        steal=bool(args.get("steal")),
        force=bool(args.get("force")),
        anonymous_owner=True,
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
        )
        if key in args
    }
    return repo.update_ticket(
        conn,
        code,
        actor=actor,
        session_id=session,
        **fields,
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
    return repo.transition(
        conn,
        code,
        status,
        note=args.get("note"),
        actor=actor,
        actor_type="agente",
        session_id=session,
        force=bool(args.get("force")),
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
    return repo.complete_ticket(
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
    return repo.comment_ticket(
        conn,
        code,
        body=body,
        author=actor,
        actor_type="agente",
        session_id=session,
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
    return repo.link_ticket(
        conn,
        code,
        kind=kind,
        ref=ref,
        actor=actor,
        actor_type="agente",
        session_id=session,
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
    return {"ticket": repo.next_ticket(conn, project=args.get("project"))}


# ---------------------------------------------------------------------------
# Tabla de Despacho (Registry)
# ---------------------------------------------------------------------------

TOOL_DISPATCH: dict[str, ToolHandler] = {
    "create": _handle_create,
    "list": _handle_list,
    "get": _handle_get,
    "claim": _handle_claim,
    "update": _handle_update,
    "transition": _handle_transition,
    "complete": _handle_complete,
    "comment": _handle_comment,
    "link": _handle_link,
    "stats": _handle_stats,
    "next": _handle_next,
}


# ---------------------------------------------------------------------------
# Punto de Entrada Principal para Despacho de Herramientas
# ---------------------------------------------------------------------------


def call_tool(
    name: str, args: dict[str, Any] | None = None, meta: Any = None
) -> dict[str, Any]:
    """Despacha la ejecución de una herramienta MCP utilizando TOOL_DISPATCH."""
    args = args or {}

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
            rows = repo.list_tickets(
                conn, statuses=["nuevo", "pendiente"], limit=100
            )
            data: Any = {"count": len(rows), "tickets": rows}
        elif uri == "taiki://stats":
            data = repo.stats(conn)
        elif uri.startswith("taiki://ticket/"):
            code = unquote(uri.removeprefix("taiki://ticket/"))
            data = repo.get_ticket(conn, code)
        else:
            raise domain.NotFoundError(f"Recurso desconocido: {uri}")
    finally:
        conn.close()

    return {
        "contents": [
            {"uri": uri, "mimeType": "application/json", "text": render.to_json(data)}
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
            rows = repo.list_tickets(conn, statuses=["completado"], limit=20)
            text = _review_prompt(rows)
            description = "Tickets completados pendientes de confirmación"
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
            "registrado, créalo antes con taiki_create."
        )
    body = ticket.get("body") or "(sin descripción)"
    return f"""\
Trabaja este ticket:

{ticket['code']} — {ticket['title']}
Estado: {ticket['status']} | Prioridad: {ticket['priority']} | Tipo: {ticket['kind']}
Proyecto: {ticket.get('project') or '-'}

{body}

Pasos:
1. Reclámalo con taiki_claim(code="{ticket['code']}", agent="<tu nombre>").
2. Inspecciona el código y aplica el cambio mínimo.
3. Verifica el resultado.
4. Ciérralo con taiki_complete(code="{ticket['code']}", resolution="...", commit="...").
No lo confirmes: esa transición la hace el usuario con el CLI.
"""


def _review_prompt(rows: list[dict[str, Any]]) -> str:
    if not rows:
        return "No hay tickets completados pendientes de confirmación."
    lines = ["Tickets completados pendientes de confirmación del usuario:", ""]
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
]
