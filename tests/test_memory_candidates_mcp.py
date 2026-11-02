"""Contrato MCP de candidatos: tool read-only, recurso, prompt y envelope."""

from __future__ import annotations

import json

import server as mcp_server
from mcp_layer import handlers, memory as memory_handlers, schemas
from ticketlib import domain, repo
from tests.support import Harness


def _tool(name: str) -> dict:
    return next(tool for tool in schemas.TOOLS if tool["name"] == name)


def _raises(exc_type, func) -> bool:
    try:
        func()
    except exc_type:
        return True
    return False


def _rework(conn, title: str, why: str, *, kind: str = "funcionalidad") -> dict:
    ticket = repo.create_ticket(conn, title=title, kind=kind)
    repo.transition(
        conn, ticket["code"], "en_progreso", actor="worker", actor_type="agente"
    )
    repo.complete_ticket(
        conn, ticket["code"], resolution="primera entrega", actor="worker"
    )
    repo.transition(
        conn,
        ticket["code"],
        "verificado",
        note="gates ok",
        actor="conductor",
        actor_type="agente",
    )
    repo.transition(
        conn,
        ticket["code"],
        "en_progreso",
        note=why,
        actor="usuario",
        actor_type="usuario",
    )
    return ticket


def _catalog(h: Harness) -> None:
    tool = _tool("memory_candidates")
    h.eq(
        (tool.get("annotations") or {}).get("readOnlyHint"),
        True,
        "memory_candidates es read-only",
    )
    h.ok(
        "memory_candidates" not in handlers.MUTATING_TOOLS,
        "memory_candidates no se trata como mutación",
    )
    h.ok(
        tool["inputSchema"]["additionalProperties"] is False
        and tool["inputSchema"]["properties"]["mode"]["enum"] == ["list", "review"]
        and tool["inputSchema"]["properties"]["limit"]["default"] == 20,
        "el esquema estricto expone modo y límite",
    )
    h.ok(
        "memory_candidates" in handlers.TOOL_DISPATCH
        and handlers.TOOL_DISPATCH["memory_candidates"]
        is memory_handlers.handle_memory_candidates,
        "el handler se registra sin duplicar dispatch",
    )
    h.ok(
        "memory_candidates(mode=review)" in schemas.INSTRUCTIONS,
        "las instrucciones incluyen la revisión periódica",
    )
    h.ok(
        any(
            resource["uri"] == "taiki://memory/candidates"
            for resource in schemas.RESOURCES
        ),
        "resources/list expone el recurso de candidatos",
    )
    h.ok(
        any(prompt["name"] == "taiki:curate" for prompt in schemas.PROMPTS),
        "prompts/list expone taiki:curate",
    )


def _tool_calls(h: Harness) -> None:
    rework = _rework(h.conn, "Rework MCP candidatos", "faltó el caso límite")
    rejection = _rework(
        h.conn, "Rechazo MCP candidatos", "faltó el test", kind="bug"
    )
    repo.comment_ticket(
        h.conn,
        rejection["code"],
        "Rechazado en revisión: falta el test",
        author="usuario",
        actor_type="usuario",
    )

    before = h.conn.execute("SELECT COUNT(*) FROM memories").fetchone()[0]
    listed = handlers.call_tool("memory_candidates", {})
    after = h.conn.execute("SELECT COUNT(*) FROM memories").fetchone()[0]
    h.eq(before, 0, "el tool no escribe memorias al listar")
    h.eq(after, 0, "el tool sigue sin escribir memorias")
    h.eq(set(listed), {"count", "candidates"}, "el envelope de list es estable")
    h.eq(
        listed["count"],
        len(listed["candidates"]),
        "el count de candidatos MCP coincide",
    )
    rework_item = next(
        item for item in listed["candidates"] if item["rule"] == "rework"
    )
    h.ok(
        any(
            item["kind"] == "ticket" and item["ref"] == rework["code"]
            for item in rework_item["evidence"]
        ),
        "el candidato MCP incluye evidencia",
    )
    h.ok(
        rework_item["sample_size"] >= 1
        and 0 <= rework_item["confidence"] <= 1,
        "el candidato MCP incluye muestra y confianza",
    )

    filtered = handlers.call_tool("memory_candidates", {"kind": "pitfall"})
    h.ok(
        filtered["count"] >= 1
        and all(
            item["kind_sugerido"] == "pitfall"
            for item in filtered["candidates"]
        ),
        "kind filtra por tipo sugerido",
    )
    limited = handlers.call_tool("memory_candidates", {"limit": 1})
    h.eq(limited["count"], 1, "limit acota la lista")
    windowed = handlers.call_tool("memory_candidates", {"since": "30d"})
    h.eq(windowed["count"], listed["count"], "since acepta una ventana en días")
    h.ok(
        _raises(
            domain.ValidationError,
            lambda: handlers.call_tool("memory_candidates", {"since": "0d"}),
        ),
        "since inválido se rechaza",
    )
    h.ok(
        _raises(
            domain.ValidationError,
            lambda: handlers.call_tool("memory_candidates", {"mode": "otra"}),
        ),
        "mode inválido se rechaza en el esquema",
    )
    h.ok(
        _raises(
            domain.ValidationError,
            lambda: handlers.call_tool("memory_candidates", {"kind": "otro"}),
        ),
        "kind fuera de MEMORY_KINDS se rechaza",
    )
    h.ok(
        _raises(
            domain.ValidationError,
            lambda: handlers.call_tool("memory_candidates", {"limit": 0}),
        ),
        "limit no positivo se rechaza",
    )

    review = handlers.call_tool("memory_candidates", {"mode": "review"})
    h.eq(
        set(review),
        {
            "generated_at",
            "candidates",
            "current_memories",
            "recent_tickets",
            "thresholds",
            "rules",
            "instructions",
        },
        "mode=review devuelve el paquete completo",
    )
    h.ok(review["candidates"], "el paquete reutiliza los candidatos")
    one = handlers.call_tool("memory_candidates", {"mode": "review", "limit": 1})
    h.eq(len(one["recent_tickets"]), 1, "mode=review acota los tickets recientes")

    envelope = mcp_server._tool_result(
        handlers.call_tool("memory_candidates", {})
    )
    payload = json.loads(envelope["content"][0]["text"])
    h.ok(
        set(envelope) == {"content"}
        and "structuredContent" not in envelope
        and payload["count"] == listed["count"],
        "el envelope MCP no duplica el payload",
    )


def _resource_and_prompt(h: Harness) -> None:
    resource = handlers.read_resource("taiki://memory/candidates")
    payload = json.loads(resource["contents"][0]["text"])
    h.eq(
        resource["contents"][0]["mimeType"],
        "application/json",
        "el recurso de candidatos es JSON",
    )
    h.ok(
        {"count", "candidates"} <= set(payload) and payload["count"] >= 1,
        "el recurso devuelve la lista de candidatos",
    )

    prompt = handlers.get_prompt("taiki:curate")
    text = prompt["messages"][0]["content"]["text"]
    h.eq(prompt["description"], "Revisión de candidatos y propuesta de lecciones", "el prompt se describe")
    h.ok(
        "máximo 5" in text
        and "evidencia" in text.lower()
        and "no apruebes" in text.lower()
        and "no edites código" in text.lower()
        and "memory_candidates(mode=review)" in text,
        "el prompt exige límite, evidencia y anti-aprendizaje",
    )


def run(h: Harness) -> None:
    _catalog(h)
    _tool_calls(h)
    _resource_and_prompt(h)
