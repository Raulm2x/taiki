"""Utilidades compartidas por los manejadores MCP por dominio."""

from __future__ import annotations

import os
import sqlite3
from pathlib import Path
from typing import Any, Callable

from ticketlib import presentation, workspace
from ticketlib.util import normalize_agent

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
    """Raíz del proyecto de la DB activa, no la del cwd del servidor.

    Con ``TAIKI_DB``/``TICKETS_DB`` apuntando a ``<proyecto>/.taiki/taiki.db``
    la raíz es ese proyecto, no el del directorio de trabajo: si no, un
    ``worktree_create`` sobre otra base crearía worktrees y ramas en el
    repositorio real.
    """
    info = workspace.resolve()
    db_file = info.get("db")
    if info.get("source") == "override" and db_file is not None:
        folder = Path(db_file).expanduser()
        if folder.parent.name == workspace.MARKER:
            return folder.parent.parent
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
