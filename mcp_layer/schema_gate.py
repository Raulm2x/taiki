"""Catálogo MCP de gate_run."""

from __future__ import annotations

from .schema_common import ToolSpec

GATE_SPECS = [
    ToolSpec(
        "gate_run",
        "Ejecuta los gates declarados (workspace o ticket) y devuelve "
        "salida estructurada; solo comandos declarados.",
        {
            "name": {
                "type": "string",
                "maxLength": 80,
                "description": "Gate declarado; sin nombre, todos (agregado)",
            },
            "ticket": {
                "type": "string",
                "maxLength": 40,
                "description": "Ticket cuyos gates se resuelven",
            },
            "timeout": {
                "type": "integer",
                "minimum": 1,
                "maximum": 3600,
                "description": "Segundos por gate (defecto 120)",
            },
        },
        read_only=True,
    ),
]
