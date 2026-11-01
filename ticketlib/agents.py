"""Nombres y colores de los agentes (presentación compartida)."""

from __future__ import annotations

import zlib
from typing import Any

from .domain import KINDS

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


# ── Registro de capacidades ────────────────────────────────────────────────
# Cada agente declara qué tipos de trabajo hace, cuántos writers admite en
# paralelo, qué modelos usa y su coste relativo. El conductor lo consulta para
# asignar tareas; ``worker`` es el respaldo cuando no hay una coincidencia.
FALLBACK_AGENT = "worker"
DEFAULT_MAX_PARALLEL = 2
CAPABILITY_KEYS = (
    "name",
    "model",
    "variant",
    "strengths",
    "max_parallel",
    "paths",
    "cost",
)

DEFAULT_AGENTS: dict[str, dict[str, Any]] = {
    "worker": {
        "model": None,
        "variant": None,
        "strengths": [
            "mantenimiento",
            "funcionalidad",
            "bug",
            "documentacion",
            "seguridad",
            "otro",
        ],
        "max_parallel": 2,
        "paths": [],
        "cost": 1.0,
    },
    "reviewer": {
        "model": None,
        "variant": None,
        "strengths": [],
        "max_parallel": 1,
        "paths": [],
        "cost": 0.5,
    },
    "opencode": {
        "model": None,
        "variant": None,
        "strengths": ["mantenimiento", "funcionalidad", "bug", "documentacion"],
        "max_parallel": 2,
        "paths": [],
        "cost": 1.0,
    },
    "antigravity": {
        "model": None,
        "variant": None,
        "strengths": ["funcionalidad", "documentacion", "mantenimiento"],
        "max_parallel": 2,
        "paths": [],
        "cost": 0.8,
    },
    "claude": {
        "model": None,
        "variant": None,
        "strengths": ["bug", "seguridad", "funcionalidad"],
        "max_parallel": 2,
        "paths": [],
        "cost": 2.0,
    },
    "gemini": {
        "model": None,
        "variant": None,
        "strengths": ["documentacion", "mantenimiento"],
        "max_parallel": 2,
        "paths": [],
        "cost": 0.7,
    },
    "codex": {
        "model": None,
        "variant": None,
        "strengths": ["bug", "mantenimiento", "seguridad"],
        "max_parallel": 2,
        "paths": [],
        "cost": 1.5,
    },
}


def _capability(name: str, data: dict[str, Any]) -> dict[str, Any]:
    """Valida y normaliza una capacidad; un error explica el campo exacto."""
    from .domain import ValidationError

    unknown = sorted(set(data) - set(CAPABILITY_KEYS))
    if unknown:
        raise ValidationError(
            f"agents.{name}: claves desconocidas {', '.join(unknown)}; "
            f"válidas: {', '.join(CAPABILITY_KEYS)}"
        )
    strengths = data.get("strengths", [])
    if not isinstance(strengths, list) or any(
        not isinstance(item, str) for item in strengths
    ):
        raise ValidationError(f"agents.{name}.strengths debe ser una lista de tipos")
    invalid = [item for item in strengths if item not in KINDS]
    if invalid:
        raise ValidationError(
            f"agents.{name}.strengths tiene tipos desconocidos: "
            f"{', '.join(invalid)}"
        )
    max_parallel = data.get("max_parallel", DEFAULT_MAX_PARALLEL)
    if (
        isinstance(max_parallel, bool)
        or not isinstance(max_parallel, int)
        or not 1 <= max_parallel <= 8
    ):
        raise ValidationError(
            f"agents.{name}.max_parallel debe ser un entero entre 1 y 8"
        )
    paths = data.get("paths", [])
    if not isinstance(paths, list) or any(
        not isinstance(item, str) or not item.strip() for item in paths
    ):
        raise ValidationError(f"agents.{name}.paths debe ser una lista de rutas")
    cost = data.get("cost", 1.0)
    if isinstance(cost, bool) or not isinstance(cost, (int, float)) or cost < 0:
        raise ValidationError(f"agents.{name}.cost debe ser un número >= 0")
    for field in ("model", "variant"):
        value = data.get(field)
        if value is not None and (
            not isinstance(value, str) or not value.strip()
        ):
            raise ValidationError(
                f"agents.{name}.{field} debe ser texto no vacío o null"
            )
    return {
        "name": name,
        "model": data.get("model"),
        "variant": data.get("variant"),
        "strengths": list(strengths),
        "max_parallel": max_parallel,
        "paths": list(paths),
        "cost": float(cost),
    }


def registry(config: dict[str, Any] | None = None) -> dict[str, dict[str, Any]]:
    """Registro validado: defaults + ``config["agents"]`` (dict o lista)."""
    from .domain import ValidationError

    raw = (config or {}).get("agents")
    overrides: dict[str, dict[str, Any]] = {}
    if raw is None:
        pass
    elif isinstance(raw, dict):
        for name, value in raw.items():
            if not isinstance(value, dict):
                raise ValidationError(
                    f"agents.{name} debe ser un objeto con sus capacidades"
                )
            overrides[str(name)] = value
    elif isinstance(raw, list):
        for item in raw:
            if not isinstance(item, dict) or not item.get("name"):
                raise ValidationError(
                    "agents debe ser una lista de objetos con 'name'"
                )
            name = str(item["name"])
            overrides[name] = {
                key: value for key, value in item.items() if key != "name"
            }
    else:
        raise ValidationError("config.agents debe ser un objeto o una lista")

    merged = {
        name: dict(data) for name, data in DEFAULT_AGENTS.items()
    }
    for name, value in overrides.items():
        merged[name] = {**merged.get(name, {}), **value}
    return {name: _capability(name, data) for name, data in merged.items()}


def capability(
    name: str, config: dict[str, Any] | None = None
) -> dict[str, Any]:
    """Capacidad de un agente; un nombre desconocido falla con la lista."""
    from .domain import ValidationError

    table = registry(config)
    key = (name or "").strip().lower()
    if key not in table:
        raise ValidationError(
            f"Agente desconocido: {name!r}. "
            f"Disponibles: {', '.join(sorted(table))}"
        )
    return table[key]


def recommend(
    kind: str, config: dict[str, Any] | None = None
) -> dict[str, Any]:
    """Agente recomendado para un tipo: el más barato que lo cubre."""
    from .domain import ValidationError
    from .util import check_enum

    check_enum(kind, KINDS, "tipo")
    table = registry(config)
    candidates = [
        item for item in table.values() if kind in item["strengths"]
    ]
    if not candidates:
        fallback = table.get(FALLBACK_AGENT)
        if fallback is not None:
            return fallback
        raise ValidationError(
            f"Ningún agente cubre el tipo {kind!r} y falta el respaldo "
            f"{FALLBACK_AGENT!r}"
        )
    return min(
        candidates,
        key=lambda item: (item["cost"], -item["max_parallel"], item["name"]),
    )
