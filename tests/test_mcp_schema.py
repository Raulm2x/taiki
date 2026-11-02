"""Contrato del catálogo MCP y resolución de sesión."""

from __future__ import annotations

import os

from mcp_layer import handlers, schemas
from ticketlib import repo
from tests.support import Harness


def run(h: Harness) -> None:
    # ── 5. MCP Handlers & Session Resolution Suite ───────────────────────
    # Catálogo MCP: nombres estables y anotaciones solo donde aportan.
    expected_catalog = {
        "create", "list", "get", "claim", "update", "transition", "complete",
        "comment", "link", "stats", "next", "agents", "status", "whoami",
        "heartbeat", "checkpoint", "retry", "reassign", "schedule",
        "worktree_create", "worktree_list", "promote", "scratch_drop",
        "health", "brief", "memory_search", "memory_write",
        "memory_playbook", "memory_checklist", "memory_candidates",
        "memory_stats",
    }
    catalog_names = {tool["name"] for tool in schemas.TOOLS}
    h.eq(
        catalog_names,
        expected_catalog,
        "tools/list expone todas las herramientas esperadas",
    )
    read_only = {
        tool["name"]
        for tool in schemas.TOOLS
        if (tool.get("annotations") or {}).get("readOnlyHint") is True
    }
    h.eq(
        read_only,
        {
            "list", "get", "stats", "next", "agents", "schedule",
            "status", "whoami", "worktree_list", "health", "brief",
            "memory_search", "memory_playbook", "memory_checklist",
            "memory_candidates", "memory_stats",
        },
        "readOnlyHint solo se anota en las herramientas de lectura",
    )
    h.ok(
        all(
            set(tool) <= {"name", "description", "inputSchema", "annotations"}
            and set(tool.get("annotations") or {}) <= {"readOnlyHint"}
            for tool in schemas.TOOLS
        ),
        "el catálogo no emite anotaciones ni claves muertas",
    )
    h.eq(
        set(handlers.TOOL_DISPATCH),
        {spec.name for spec in schemas.TOOL_SPECS},
        "TOOL_DISPATCH se deriva del catálogo",
    )
    h.eq(
        handlers.MUTATING_TOOLS,
        frozenset(
            spec.name for spec in schemas.TOOL_SPECS if not spec.read_only
        ),
        "MUTATING_TOOLS se deriva de read_only",
    )
    transition_tool = next(
        tool for tool in schemas.TOOLS if tool["name"] == "transition"
    )
    h.ok(
        "verificado"
        in transition_tool["inputSchema"]["properties"]["status"]["enum"]
        and "verificado" in schemas.INSTRUCTIONS,
        "el catálogo MCP expone y documenta el estado verificado",
    )

    # Precedencia en resolve_session
    # 1. meta > args > env
    sess_meta = handlers.resolve_session(
        meta={"sessionId": "meta-session-uuid"},
        args={"sessionId": "args-session-uuid"},
    )
    h.ok(sess_meta == "meta-session-uuid", "meta['sessionId'] tiene precedencia sobre args")

    # Claves alternativas en meta
    h.ok(
        handlers.resolve_session(meta={"session_id": "meta-snake"}) == "meta-snake",
        "resolve_session admite 'session_id' en meta",
    )
    h.ok(
        handlers.resolve_session(meta={"ai.opencode/sessionID": "legacy-opencode"}) == "legacy-opencode",
        "resolve_session admite 'ai.opencode/sessionID' en meta",
    )

    # 2. args > env
    isolated = (
        "ANTIGRAVITY_CONVERSATION_ID",
        "AGY_SESSION_ID",
        "SESSION_ID",
        "OPENCODE_SESSION_ID",
    )
    saved = {name: os.environ.pop(name, None) for name in isolated}
    try:
        os.environ["AGY_SESSION_ID"] = "env-agy-sess"
        sess_arg = handlers.resolve_session(
            meta=None,
            args={"session_id": "args-session-override"},
        )
        h.ok(sess_arg == "args-session-override", "args tiene precedencia sobre variables de entorno")

        # 3. Fallback a entorno (ANTIGRAVITY_CONVERSATION_ID > AGY_SESSION_ID > SESSION_ID)
        os.environ["ANTIGRAVITY_CONVERSATION_ID"] = "env-antigravity-conv"
        h.ok(
            handlers.resolve_session() == "env-antigravity-conv",
            "resolve_session cae a ANTIGRAVITY_CONVERSATION_ID en entorno",
        )
        os.environ.pop("ANTIGRAVITY_CONVERSATION_ID", None)

        sess_env_agy = handlers.resolve_session()
        h.ok(sess_env_agy == "env-agy-sess", "resolve_session cae a AGY_SESSION_ID en entorno")
        os.environ.pop("AGY_SESSION_ID", None)

        os.environ["SESSION_ID"] = "env-std-sess"
        sess_env_std = handlers.resolve_session()
        h.ok(sess_env_std == "env-std-sess", "resolve_session cae a SESSION_ID si AGY_SESSION_ID no está")
        os.environ.pop("SESSION_ID", None)

        # 4. Sin meta, args ni env -> None
        h.ok(
            handlers.resolve_session(meta={}, args={}) is None,
            "sin datos de sesión devuelve None",
        )
    finally:
        for name, value in saved.items():
            if value is not None:
                os.environ[name] = value

    # 5. Normalización de espacios y fallback ante valores vacíos
    h.ok(
        handlers.resolve_session(meta={"sessionId": "   trimmed-uuid   "}) == "trimmed-uuid",
        "resolve_session recorta espacios en blanco en la sesión",
    )
    h.ok(
        handlers.resolve_session(meta={"sessionId": "   "}, args={"session_id": "fallback-arg"}) == "fallback-arg",
        "meta vacío cae al identificador provisto en args",
    )

    # ── Dependencias y capacidades por MCP ──────────────────────────────
    base = repo.create_ticket(h.conn, title="Base MCP", project="ready-mcp")
    dependent = repo.create_ticket(
        h.conn, title="Dependiente MCP", project="ready-mcp"
    )
    repo.link_ticket(
        h.conn, dependent["code"], "depende", base["code"]
    )
    listed = handlers.call_tool(
        "list", {"project": "ready-mcp", "ready": True, "limit": 10}
    )
    listed_codes = {ticket["code"] for ticket in listed["tickets"]}
    h.ok(
        base["code"] in listed_codes and dependent["code"] not in listed_codes,
        "list ready filtra las dependencias abiertas por MCP",
    )
    following = handlers.call_tool("next", {"project": "ready-mcp"})
    h.ok(
        following["ticket"]["code"] == base["code"],
        "next salta la tarea bloqueada por MCP",
    )
    catalog = handlers.call_tool("agents", {"kind": "bug"})
    h.ok(
        catalog["agents"]
        and catalog["recommended"]["name"] in {
            item["name"] for item in catalog["agents"]
        },
        "agents expone el registro y recomienda un agente",
    )
