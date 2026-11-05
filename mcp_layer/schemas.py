"""Definiciones MCP: herramientas, recursos, prompts e instrucciones.

La fachada conserva los nombres públicos; el catálogo vive en módulos por
dominio para no superar el límite de tamaño por módulo.
"""

from __future__ import annotations

from .schema_common import (
    AGENT,
    CODE,
    DETAIL,
    FORCE,
    INSTRUCTIONS,
    SESSION,
    STATUS,
    ToolSpec,
)
from .schema_memory import MEMORY_SPECS
from .schema_meta import (
    PROMPTS,
    RESOURCES,
    RESOURCE_TEMPLATES,
    SKILL_SPECS,
)
from .schema_reorg import REORG_SPECS
from .schema_tickets import TICKET_SPECS

TOOL_SPECS = [*TICKET_SPECS, *REORG_SPECS, *MEMORY_SPECS, *SKILL_SPECS]

TOOLS = [spec.schema() for spec in TOOL_SPECS]

__all__ = [
    "AGENT",
    "CODE",
    "DETAIL",
    "FORCE",
    "INSTRUCTIONS",
    "PROMPTS",
    "RESOURCES",
    "RESOURCE_TEMPLATES",
    "SESSION",
    "STATUS",
    "TOOLS",
    "TOOL_SPECS",
    "ToolSpec",
]
