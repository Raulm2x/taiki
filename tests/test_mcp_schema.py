"""Contrato del catálogo MCP y resolución de sesión."""

from __future__ import annotations

import os
from pathlib import Path

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
        "reorg_plan", "reorg_apply",
        "health", "brief", "memory_search", "memory_write",
        "memory_playbook", "memory_checklist", "memory_candidates",
        "memory_stats", "skills_suggest",
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
            "reorg_plan",
            "memory_search", "memory_playbook", "memory_checklist",
            "memory_candidates", "memory_stats", "skills_suggest",
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
    h.ok(
        set(handlers.TOOL_DISPATCH) == {spec.name for spec in schemas.TOOL_SPECS}
        and handlers.MUTATING_TOOLS
        == frozenset(
            spec.name for spec in schemas.TOOL_SPECS if not spec.read_only
        ),
        "TOOL_DISPATCH y MUTATING_TOOLS se derivan del catálogo",
    )
    module_root = Path(__file__).resolve().parents[1] / "mcp_layer"
    line_counts = {
        path.relative_to(module_root).as_posix(): len(
            path.read_text(encoding="utf-8").splitlines()
        )
        for path in module_root.rglob("*.py")
    }
    oversized = sorted(
        f"{name} ({lines})"
        for name, lines in line_counts.items()
        if lines > 700
    )
    h.ok(
        not oversized,
        "ningún módulo MCP supera 700 líneas"
        + (f": {', '.join(oversized)}" if oversized else ""),
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
    reorg_plan = next(
        tool for tool in schemas.TOOLS if tool["name"] == "reorg_plan"
    )
    reorg_apply = next(
        tool for tool in schemas.TOOLS if tool["name"] == "reorg_apply"
    )
    reorg_actions = {"move", "renumber", "promote", "demote", "purge"}
    h.ok(
        set(reorg_plan["inputSchema"]["properties"]["action"]["enum"])
        == reorg_actions
        and reorg_plan["inputSchema"]["required"] == ["action"]
        and reorg_apply["inputSchema"]["required"] == ["action", "apply"]
        and reorg_plan["inputSchema"]["additionalProperties"] is False
        and reorg_apply["inputSchema"]["additionalProperties"] is False,
        "reorg_plan/reorg_apply declaran acción, apply y cierran el esquema",
    )

    # Precedencia en resolve_session: meta > args > env y claves alternativas
    sess_meta = handlers.resolve_session(
        meta={"sessionId": "meta-session-uuid"},
        args={"sessionId": "args-session-uuid"},
    )
    h.ok(
        sess_meta == "meta-session-uuid"
        and handlers.resolve_session(meta={"session_id": "meta-snake"})
        == "meta-snake"
        and handlers.resolve_session(
            meta={"ai.opencode/sessionID": "legacy-opencode"}
        )
        == "legacy-opencode",
        "meta['sessionId'] manda y admite session_id y ai.opencode/sessionID",
    )

    # args > env; sin args, la cadena de fallback respeta su orden
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
        os.environ["ANTIGRAVITY_CONVERSATION_ID"] = "env-antigravity-conv"
        sess_conv = handlers.resolve_session()
        os.environ.pop("ANTIGRAVITY_CONVERSATION_ID", None)
        sess_env_agy = handlers.resolve_session()
        os.environ.pop("AGY_SESSION_ID", None)
        os.environ["SESSION_ID"] = "env-std-sess"
        sess_env_std = handlers.resolve_session()
        os.environ.pop("SESSION_ID", None)
        h.ok(
            sess_arg == "args-session-override"
            and sess_conv == "env-antigravity-conv"
            and sess_env_agy == "env-agy-sess"
            and sess_env_std == "env-std-sess",
            "args gana al entorno y el fallback usa conv, agy y session_id",
        )
        h.ok(
            handlers.resolve_session(meta={}, args={}) is None,
            "sin datos de sesión devuelve None",
        )
    finally:
        for name, value in saved.items():
            if value is not None:
                os.environ[name] = value

    # Normalización de espacios y fallback ante valores vacíos
    h.ok(
        handlers.resolve_session(meta={"sessionId": "   trimmed-uuid   "})
        == "trimmed-uuid"
        and handlers.resolve_session(
            meta={"sessionId": "   "}, args={"session_id": "fallback-arg"}
        )
        == "fallback-arg",
        "resolve_session recorta espacios y un meta vacío cae a args",
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

    # ── Contrato MCP de aislamiento y scope ─────────────────────────────
    h.ok(
        "authorize" not in catalog_names and "review" not in catalog_names,
        "el aislamiento no expone authorize ni review",
    )
    created = handlers.call_tool(
        "create",
        {
            "title": "Ticket MCP aislado",
            "isolation": "scope",
            "resources": ["package:git"],
            "reversal": {"kind": "manual", "ref": "T-0060.01"},
        },
    )
    claimed = handlers.call_tool(
        "claim", {"code": created["code"], "agent": "mcp-iso"}
    )
    h.ok(
        created["isolation"] == "scope"
        and created["resources"] == ["package:git"]
        and created["reversal"]["kind"] == "manual"
        and claimed["isolation"] == "scope"
        and isinstance(claimed.get("scope"), list)
        and claimed["scope"],
        "create acepta isolation/resources/reversal y claim devuelve scope",
    )
    updated = handlers.call_tool(
        "update",
        {
            "code": created["code"],
            "isolation": "none",
            "resources": ["service:sshd"],
            "reversal": {"kind": "command", "command": "true"},
        },
    )
    shown = handlers.call_tool("get", {"code": created["code"]})
    listed = handlers.call_tool(
        "list", {"query": "Ticket MCP aislado", "limit": 5}
    )
    h.ok(
        updated["isolation"] == "none"
        and updated["resources"] == ["service:sshd"]
        and updated["reversal"]["kind"] == "command"
        and shown["isolation"] == "none"
        and shown["resources"] == ["service:sshd"]
        and isinstance(shown.get("scope"), list)
        and listed.get("scope_roots")
        and any(
            item.get("isolation") == "none"
            for item in listed["tickets"]
        ),
        "update, get y list conservan isolation, recursos y scope",
    )
