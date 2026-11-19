"""Catálogo MCP de gate_run."""

from __future__ import annotations

from .schema_common import ToolSpec

GATE_SPECS = [
    ToolSpec(
        "gate_run",
        "Ejecuta los gates declarados del workspace o del ticket "
        "(verify.command, gates) y devuelve salida estructurada. Solo ejecuta "
        "comandos declarados; solo lectura del estado.",
        {
            "name": {
                "type": "string",
                "maxLength": 80,
                "description": (
                    "Nombre del gate declarado; si se omite se ejecutan todos "
                    "y se devuelve el resultado agregado"
                ),
            },
            "ticket": {
                "type": "string",
                "maxLength": 40,
                "description": (
                    "Código del ticket cuyos gates se resuelven (bloque gates "
                    "del body y worktree de su sesión)"
                ),
            },
            "timeout": {
                "type": "integer",
                "minimum": 1,
                "maximum": 3600,
                "description": "Segundos de espera por gate (por defecto 120)",
            },
        },
        read_only=True,
    ),
]
