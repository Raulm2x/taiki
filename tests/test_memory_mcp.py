"""Contrato MCP de memoria: tools, recursos, prompt, hints y nudge."""

from __future__ import annotations

import json

from mcp_layer import handlers, memory as memory_handlers, schemas
from ticketlib import domain, repo, runtime
from ticketlib.memory import add_memory
from tests.support import Harness


def _tool(name: str) -> dict:
    return next(tool for tool in schemas.TOOLS if tool["name"] == name)


def _raises(exc_type, func) -> bool:
    try:
        func()
    except exc_type:
        return True
    return False


def run(h: Harness) -> None:
    names = {tool["name"] for tool in schemas.TOOLS}
    read_only = {
        tool["name"]
        for tool in schemas.TOOLS
        if (tool.get("annotations") or {}).get("readOnlyHint") is True
    }
    h.ok(
        {"brief", "memory_search", "memory_write"} <= names
        and read_only & {"brief", "memory_search", "memory_write"}
        == {"brief", "memory_search"},
        "tools/list expone memoria y solo brief/memory_search son de lectura",
    )
    operations = _tool("memory_write")["inputSchema"]["properties"]["operations"]
    search_props = _tool("memory_search")["inputSchema"]["properties"]
    write_props = operations["items"]["properties"]
    h.ok(
        set(memory_handlers.HANDLERS) <= set(handlers.TOOL_DISPATCH)
        and all(
            _tool(name)["inputSchema"]["additionalProperties"] is False
            for name in ("brief", "memory_search", "memory_write")
        )
        and operations["items"]["additionalProperties"] is False
        and search_props["ticket_kinds"]["items"]["enum"] == list(domain.KINDS)
        and write_props["ticket_kinds"]["items"]["enum"] == list(domain.KINDS)
        and "memory_write" in handlers.MUTATING_TOOLS
        and "brief" not in handlers.MUTATING_TOOLS,
        "despacho, esquemas estrictos y mutating derivados del catálogo",
    )

    invalid = (
        lambda: handlers.call_tool("memory_search", {"nope": 1}),
        lambda: handlers.call_tool(
            "memory_write", {"operations": [{"action": "add", "nope": 1}]}
        ),
        lambda: handlers.call_tool("brief", {"section": "otra"}),
        lambda: handlers.call_tool(
            "memory_write", {"operations": [{"action": "borrar", "code": "M-1"}]}
        ),
        lambda: handlers.call_tool(
            "memory_write", {"operations": [{"action": "patch", "title": "x"}]}
        ),
        lambda: handlers.call_tool("memory_write", {"operations": []}),
    )
    h.ok(
        all(_raises(domain.ValidationError, func) for func in invalid),
        "argumentos, operaciones y secciones inválidas se rechazan",
    )

    ticket = repo.create_ticket(
        h.conn,
        title="Memoria MCP",
        kind="funcionalidad",
        paths=["mcp_layer/memory.py"],
        tags=["memoria"],
    )
    hint_match = add_memory(
        h.conn,
        title="Ruta MCP",
        content="La memoria MCP vive en mcp_layer/memory.py.",
        kind="pitfall",
        scope="project",
        paths=["mcp_layer/memory.py"],
        ticket_kinds=["bug", "funcionalidad"],
        kinds=["fact"],
        source="user",
        actor="usuario",
    )
    add_memory(
        h.conn,
        title="Otra memoria",
        content="Documentación general.",
        kind="preference",
        scope="project",
        paths=["docs/"],
        source="user",
        actor="usuario",
    )

    brief = handlers.call_tool("brief", {})
    user_brief = handlers.call_tool("brief", {"section": "user", "budget": 800})
    original_root = memory_handlers._project_root
    memory_handlers._project_root = lambda: h.tmp
    try:
        written = handlers.call_tool(
            "brief", {"write": True, "section": "project", "budget": 800}
        )
    finally:
        memory_handlers._project_root = original_root
    h.ok(
        {"text", "usage", "sections", "version", "generated_at"} <= set(brief)
        and "Brief operativo" in brief["text"]
        and user_brief["usage"]["limit"] == 800
        and written["path"].startswith(str(h.tmp)),
        "brief devuelve contrato, sección y artefacto escrito",
    )

    long_content = "detalle " * 40
    write = handlers.call_tool(
        "memory_write",
        {
            "operations": [
                {
                    "action": "add",
                    "kind": "decision",
                    "key": "decision-mcp",
                    "title": "Decisión MCP",
                    "content": long_content,
                    "evidence": [ticket["code"]],
                    "confidence": 0.9,
                },
                {
                    "action": "add",
                    "kind": "fact",
                    "key": "fact-mcp",
                    "title": "Hecho MCP",
                    "content": "El brief reutiliza el store de memoria.",
                    "ticket_kinds": ["bug"],
                    "evidence": [ticket["code"], "mcp_layer/memory.py"],
                    "confidence": 0.9,
                },
            ]
        },
    )
    proposed_id, active_id = write["ids"]

    search = handlers.call_tool("memory_search", {"query": "MCP", "limit": 10})
    active = handlers.call_tool(
        "memory_search",
        {"scope": "project", "kind": "fact", "status": "active"},
    )
    by_paths = handlers.call_tool(
        "memory_search",
        {
            "paths": ["mcp_layer/memory.py"],
            "kinds": ["fact"],
            "include_content": True,
        },
    )
    miss = handlers.call_tool(
        "memory_search", {"paths": ["no-existe/"], "limit": 5}
    )
    by_ticket = handlers.call_tool(
        "memory_search", {"query": "reutiliza", "ticket_kinds": ["bug"]}
    )
    other_ticket = handlers.call_tool(
        "memory_search", {"query": "reutiliza", "ticket_kinds": ["funcionalidad"]}
    )
    h.ok(
        search["count"] >= 3
        and all("snippet" in item and "content" not in item for item in search["memories"])
        and any(item["snippet"].endswith("…") for item in search["memories"])
        and active["count"] >= 1
        and by_paths["count"] == 1
        and by_paths["memories"][0]["code"] == hint_match["code"]
        and "content" in by_paths["memories"][0]
        and miss["count"] == 0
        and by_ticket["count"] == 1
        and other_ticket["count"] == 0,
        "memory_search combina FTS, filtros, rutas, ticket_kinds y snippets",
    )

    lifecycle = handlers.call_tool(
        "memory_write",
        {
            "operations": [
                {"action": "patch", "code": active_id, "title": "Hecho MCP v2", "pin": True},
                {"action": "feedback", "code": active_id, "helpful": True, "reason": "útil"},
                {"action": "remove", "code": proposed_id, "reason": "obsoleta"},
            ]
        },
    )
    superseded = handlers.call_tool(
        "memory_write",
        {
            "operations": [
                {
                    "action": "supersede",
                    "code": active_id,
                    "title": "Hecho MCP v3",
                    "content": "Sustituye la versión anterior.",
                    "key": "fact-mcp-v3",
                    "confidence": 0.9,
                    "evidence": [ticket["code"], "mcp_layer/memory.py"],
                }
            ]
        },
    )
    archived = handlers.call_tool(
        "memory_search", {"status": "archived", "limit": 20}
    )
    h.ok(
        write["applied"] == 1
        and write["proposed"] == 1
        and write["budget"].get("project", {}).get("limit")
        and write["warnings"]
        and lifecycle["applied"] == 3
        and lifecycle["proposed"] == 0
        and superseded["applied"] == 1
        and archived["count"] >= 1,
        "memory_write aplica política, presupuesto y ciclo patch/feedback/remove/supersede",
    )

    by_path = memory_handlers.hints(
        h.conn,
        {"code": "T-X", "paths": ["mcp_layer/memory.py"], "kind": "funcionalidad", "tags": []},
    )
    by_kind = memory_handlers.hints(
        h.conn, {"code": "T-X", "paths": [], "kind": "funcionalidad", "tags": []}
    )
    no_hints = memory_handlers.hints(
        h.conn, {"code": "T-X", "paths": ["nada/"], "kind": "documentacion", "tags": []}
    )
    original_config = memory_handlers._config
    memory_handlers._config = lambda: {"enabled": False}
    try:
        disabled = memory_handlers.hints(h.conn, {"code": "T-X"})
    finally:
        memory_handlers._config = original_config
    normal = memory_handlers.learning({"code": "T-1", "iterations": 1, "events": []})
    iterative = memory_handlers.learning(
        {"code": "T-1", "iterations": 2, "events": []}
    )
    reopened = memory_handlers.learning(
        {
            "code": "T-1",
            "iterations": 1,
            "events": [{"from_status": "verificado", "to_status": "en_progreso"}],
        }
    )
    h.ok(
        by_path
        and by_path[0]["code"] == hint_match["code"]
        and "content" not in by_path[0]
        and by_kind
        and by_kind[0]["code"] == hint_match["code"]
        and no_hints == []
        and disabled == []
        and normal is None
        and iterative["suggested"]
        and reopened["suggested"]
        and "reabrió" in reopened["reason"],
        "hints filtra por ruta/tipo y learning distingue rework de un ticket normal",
    )

    brief_resource = handlers.read_resource("taiki://brief")
    memory_resource = handlers.read_resource(f"taiki://memory/{hint_match['code']}")
    prompt = handlers.get_prompt("taiki:brief", {"section": "all"})
    h.ok(
        brief_resource["contents"][0]["mimeType"] == "text/markdown"
        and "Brief operativo" in brief_resource["contents"][0]["text"]
        and json.loads(memory_resource["contents"][0]["text"])["code"]
        == hint_match["code"]
        and "brief operativo" in prompt["messages"][0]["content"]["text"].lower(),
        "recursos de brief/memoria y prompt taiki:brief responden",
    )

    claimed = handlers.call_tool(
        "claim", {"code": ticket["code"], "agent": "worker-test"}
    )
    rework = repo.create_ticket(h.conn, title="Rework MCP", kind="bug")
    repo.transition(h.conn, rework["code"], "en_progreso", note="inicio", actor="worker-test")
    repo.complete_ticket(h.conn, rework["code"], "primera", actor="worker-test")
    repo.transition(h.conn, rework["code"], "en_progreso", note="rework", actor="worker-test")
    completed = handlers.call_tool(
        "complete",
        {"code": rework["code"], "resolution": "segunda", "agent": "worker-test"},
    )
    original_stale = runtime.is_stale
    runtime.is_stale = lambda: True
    try:
        stale = _raises(
            domain.StaleServerError,
            lambda: handlers.call_tool(
                "memory_write",
                {
                    "operations": [
                        {
                            "action": "add",
                            "kind": "fact",
                            "title": "No debe escribirse",
                            "content": "Servidor viejo.",
                            "evidence": ["README.md"],
                        }
                    ]
                },
            ),
        )
    finally:
        runtime.is_stale = original_stale
    h.ok(
        any(item["code"] == hint_match["code"] for item in claimed["hints"])
        and all("content" not in item for item in claimed["hints"])
        and completed["learning"]["suggested"]
        and stale,
        "claim adjunta hints, complete avisa de la lección y stale bloquea escrituras",
    )
