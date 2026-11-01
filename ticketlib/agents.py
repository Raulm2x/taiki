"""Nombres y colores de los agentes (presentación compartida)."""

from __future__ import annotations

import zlib
from typing import Any

# Color fijo para los agentes habituales (índices de la paleta 256).
AGENT_COLORS: dict[str, str] = {
    "opencode": "39",
    "antigravity": "141",
    "claude": "208",
    "gemini": "75",
    "codex": "114",
    "usuario": "250",
}

GENERIC_ACTORS = {"agente"}

# Paleta de respaldo con contraste suficiente sobre fondo oscuro.
AGENT_PALETTE = (
    "39",
    "45",
    "51",
    "75",
    "81",
    "105",
    "111",
    "114",
    "141",
    "147",
    "176",
    "179",
    "203",
    "208",
    "214",
    "220",
)

NO_SESSION_COLOR = "244"


def agent_color(agent: str | None) -> str:
    """Color determinista para un agente (mismo nombre, mismo color siempre)."""
    name = (agent or "").strip().lower()
    if not name:
        return NO_SESSION_COLOR
    known = AGENT_COLORS.get(name)
    if known:
        return known
    digest = zlib.crc32(name.encode("utf-8"))
    return AGENT_PALETTE[digest % len(AGENT_PALETTE)]


def agent_name(ticket: dict[str, Any]) -> str:
    """Agente efectivo: el asignado o, si aún no lo hay, el último actor.

    El actor genérico ``agente`` no cuenta como dueño: es el valor por defecto
    de las herramientas cuando no se identifica nadie.
    """
    name = str(ticket.get("assigned_to") or ticket.get("last_actor") or "").strip()
    return "" if name in GENERIC_ACTORS else name


AGENT_COMMANDS: dict[str, tuple[str, ...]] = {
    "opencode": ("opencode",),
    "antigravity": ("agy", "antigravity"),
}
