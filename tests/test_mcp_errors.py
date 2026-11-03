"""Validación de argumentos, errores estructurados y límites del MCP."""

from __future__ import annotations

import json

import server as mcp_server
from mcp_layer import errors, handlers, schemas
from ticketlib import domain, runtime
from tests.support import Harness


def _rejects(h: Harness, cases: list, message: str) -> None:
    """Un check para una tabla de entradas que deben rechazarse."""
    rejected = 0
    for case in cases:
        try:
            case()
        except domain.ValidationError:
            rejected += 1
    h.eq(rejected, len(cases), message)


def run(h: Harness) -> None:
    # ── Validación de argumentos y errores estructurados ───────────────
    _rejects(
        h,
        [
            lambda: handlers.call_tool("list", {"statuss": ["pendiente"]}),
            lambda: handlers.call_tool("list", {"limit": "dos"}),
            lambda: handlers.call_tool(
                "get", {"code": "T-0001", "include": ["inventada"]}
            ),
            lambda: handlers.call_tool(
                "create", {"title": "x", "parent_id": 3}
            ),
            lambda: handlers.call_tool("taiki_list", {"statuss": []}),
        ],
        "se rechazan argumentos desconocidos, tipos, enums y alias inválidos",
    )
    try:
        handlers.call_tool("get", {"code": "T-9999"})
    except domain.NotFoundError as exc:
        error = errors.payload(exc, tool="get")
        h.eq(
            error["error"]["code"],
            "not_found",
            "errors.payload clasifica un ticket inexistente",
        )
        h.ok(
            error["error"].get("hint"),
            "el error incluye una pista accionable",
        )
    stale = errors.payload(
        domain.StaleServerError("texto distinto: el proceso quedó viejo")
    )
    h.eq(
        stale["error"]["code"],
        "stale_server",
        "errors.payload clasifica el stale por tipo, no por mensaje",
    )
    envelope = mcp_server._tool_error(
        errors.payload(domain.ValidationError("argumento malo"), tool="list")
    )
    tool_result = mcp_server._tool_result({"a": 1, "b": [1, 2]})
    h.ok(
        envelope["isError"]
        and envelope["structuredContent"]["error"]["code"]
        == "invalid_arguments"
        and "structuredContent" not in tool_result
        and json.loads(tool_result["content"][0]["text"]) == {"a": 1, "b": [1, 2]},
        "el servidor devuelve errores estructurados y JSON compacto",
    )
    h.ok(
        all("outputSchema" not in tool for tool in schemas.TOOLS),
        "las herramientas no declaran un outputSchema genérico",
    )

    # ── Límites de tamaño y bloqueo global de escrituras con MCP viejo ──
    _rejects(
        h,
        [
            lambda: handlers.call_tool("create", {"title": "x" * 201}),
            lambda: handlers.call_tool(
                "create", {"title": "ok", "body": "x" * 100_001}
            ),
            lambda: handlers.call_tool("list", {"query": "x" * 501}),
            lambda: handlers.call_tool(
                "complete",
                {
                    "code": "T-0001",
                    "resolution": "x",
                    "files": ["f"] * 501,
                },
            ),
        ],
        "se rechazan título, body, búsqueda y listas de archivos demasiado largos",
    )
    original_stale = runtime.is_stale
    runtime.is_stale = lambda: True
    try:
        h.raises(
            domain.StaleServerError,
            lambda: handlers.call_tool("create", {"title": "MCP viejo"}),
            "un MCP viejo bloquea cualquier escritura, no solo las subtareas",
        )
        handlers.call_tool("list", {"limit": 1})
    finally:
        runtime.is_stale = original_stale
