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
# paralelo, qué modelo/variante usa, sus steps y su coste relativo. Solo hay
# perfiles que existen como archivo en ``.opencode/agents/``: el conductor
# únicamente puede lanzar ``worker*``, ``reviewer``, ``curator`` y ``merger``.
FALLBACK_AGENT = "worker"
DEFAULT_MAX_PARALLEL = 2
CAPABILITY_KEYS = (
    "name",
    "model",
    "variant",
    "variants",
    "strengths",
    "max_parallel",
    "paths",
    "cost",
    "steps",
)

# Variantes de modelo conocidas, de más barata a más capaz. Un modelo puede
# exponer un subconjunto; sin variante se usa el default del cliente.
VARIANT_LEVELS = ("low", "medium", "high", "xhigh", "max")
# Algunos catálogos exponen "none" (sin variante); no es un nivel de routing.
MODEL_VARIANTS = ("none", *VARIANT_LEVELS)
ROUTING: dict[str, str] = {
    # Documentación normal (varios documentos o una mudanza) no cabe en 16
    # pasos: va a worker-medium. ``worker-low`` queda para ediciones cortas.
    "documentacion": "medium",
    "mantenimiento": "medium",
    "funcionalidad": "medium",
    "otro": "medium",
    "bug": "high",
    "seguridad": "high",
}
STEPS_BY_LEVEL = {"low": 16, "medium": 24, "high": 40, "xhigh": 48, "max": 48}
DEFAULT_CONCURRENCY = 3

# Catálogo de modelos por defecto del proyecto. ``.taiki/config.json`` puede
# sobrescribirlo en ``models``; un modelo/variante fuera del catálogo falla.
DEFAULT_MODELS: dict[str, list[str]] = {
    "opencode-go/deepseek-v4.1-flash": ["low", "high", "max"],
}

DEFAULT_AGENTS: dict[str, dict[str, Any]] = {
    "conductor": {
        "model": "opencode-go/deepseek-v4.1-flash",
        "variant": "high",
        "strengths": [],
        "max_parallel": 3,
        "paths": [],
        "cost": 0.0,
        "steps": 40,
    },
    "worker": {
        "model": None,
        "variant": None,
        "variants": ["low", "medium", "high"],
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
        "cost": 1.2,
        "steps": 30,
    },
    "worker-low": {
        "model": "opencode-go/deepseek-v4.1-flash",
        "variant": "low",
        "strengths": ["documentacion"],
        "max_parallel": 2,
        "paths": [],
        "cost": 0.5,
        "steps": 16,
    },
    "worker-medium": {
        # deepseek no expone variante "medium": se usa el default del cliente
        # en vez de un modelo más caro por tokens.
        "model": "opencode-go/deepseek-v4.1-flash",
        "variant": None,
        "strengths": ["documentacion", "mantenimiento", "funcionalidad", "otro"],
        "max_parallel": 2,
        "paths": [],
        "cost": 1.0,
        "steps": 24,
    },
    "worker-high": {
        "model": "opencode-go/deepseek-v4.1-flash",
        "variant": "high",
        "strengths": ["bug", "seguridad"],
        "max_parallel": 1,
        "paths": [],
        "cost": 1.5,
        "steps": 40,
    },
    "reviewer": {
        "model": "opencode-go/deepseek-v4.1-flash",
        "variant": "low",
        "strengths": [],
        "max_parallel": 1,
        "paths": [],
        "cost": 0.5,
        "steps": 20,
    },
    "curator": {
        "model": "opencode-go/deepseek-v4.1-flash",
        "variant": "high",
        "strengths": [],
        "max_parallel": 1,
        "paths": [],
        "cost": 1.0,
        "steps": 24,
    },
    "merger": {
        "model": "opencode-go/deepseek-v4.1-flash",
        "variant": "high",
        "strengths": [],
        "max_parallel": 1,
        "paths": [],
        "cost": 1.0,
        "steps": 24,
    },
}


def _models_catalog(config: dict[str, Any] | None) -> dict[str, list[str]]:
    from .domain import ValidationError

    raw = (config or {}).get("models")
    if raw is None:
        return dict(DEFAULT_MODELS)
    if not isinstance(raw, dict):
        raise ValidationError("config.models debe ser un objeto modelo→variantes")
    catalog: dict[str, list[str]] = {}
    for model, variants in raw.items():
        if not isinstance(variants, list) or any(
            not isinstance(item, str) for item in variants
        ):
            raise ValidationError(
                f"models.{model} debe ser una lista de variantes"
            )
        invalid = [item for item in variants if item not in MODEL_VARIANTS]
        if invalid:
            raise ValidationError(
                f"models.{model} tiene variantes desconocidas: "
                f"{', '.join(invalid)}"
            )
        catalog[str(model)] = list(dict.fromkeys(variants))
    return catalog


def _capability(
    name: str,
    data: dict[str, Any],
    models: dict[str, list[str]],
) -> dict[str, Any]:
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
    steps = data.get("steps", STEPS_BY_LEVEL["medium"])
    if (
        isinstance(steps, bool)
        or not isinstance(steps, int)
        or not 1 <= steps <= 100
    ):
        raise ValidationError(
            f"agents.{name}.steps debe ser un entero entre 1 y 100"
        )
    model = data.get("model")
    if model is not None and (
        not isinstance(model, str) or not model.strip()
    ):
        raise ValidationError(
            f"agents.{name}.model debe ser texto no vacío o null"
        )
    if model is not None and model not in models:
        raise ValidationError(
            f"agents.{name}.model desconocido: {model!r}; "
            f"añádelo a models en la configuración"
        )
    supported = (
        models.get(model, [])
        if model is not None
        else list(data.get("variants", VARIANT_LEVELS))
    )
    variant = data.get("variant")
    if variant is not None and variant not in VARIANT_LEVELS:
        raise ValidationError(
            f"agents.{name}.variant debe ser uno de "
            f"{', '.join(VARIANT_LEVELS)} o null"
        )
    if variant is not None and supported and variant not in supported:
        raise ValidationError(
            f"agents.{name}.variant {variant!r} no existe en {model!r}; "
            f"disponibles: {', '.join(supported) or 'ninguna'}"
        )
    variants = data.get("variants", supported or list(VARIANT_LEVELS))
    if not isinstance(variants, list) or any(
        not isinstance(item, str) for item in variants
    ):
        raise ValidationError(f"agents.{name}.variants debe ser una lista")
    invalid_variants = [item for item in variants if item not in VARIANT_LEVELS]
    if invalid_variants:
        raise ValidationError(
            f"agents.{name}.variants tiene niveles desconocidos: "
            f"{', '.join(invalid_variants)}"
        )
    return {
        "name": name,
        "model": model,
        "variant": variant,
        "variants": list(dict.fromkeys(supported or variants)),
        "strengths": list(strengths),
        "max_parallel": max_parallel,
        "paths": list(paths),
        "cost": float(cost),
        "steps": steps,
    }


def registry(config: dict[str, Any] | None = None) -> dict[str, dict[str, Any]]:
    """Registro validado: defaults + ``config["agents"]`` (dict o lista)."""
    from .domain import ValidationError

    models = _models_catalog(config)
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

    merged = {name: dict(data) for name, data in DEFAULT_AGENTS.items()}
    for name, value in overrides.items():
        if name not in merged:
            raise ValidationError(
                f"agents.{name}: no existe un archivo de agente con ese nombre; "
                f"disponibles: {', '.join(sorted(merged))}"
            )
        merged[name] = {**merged[name], **value}
    return {
        name: _capability(name, data, models)
        for name, data in merged.items()
    }


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
    """Perfil que marca el routing; ``worker`` solo como respaldo.

    Coincide con ``assign`` para que el MCP no recomiende un perfil distinto
    del que se lanza. Si el perfil del nivel no cubre el tipo, cae al mínimo
    por coste entre los que sí lo cubren.
    """
    from .domain import ValidationError
    from .util import check_enum

    check_enum(kind, KINDS, "tipo")
    table = registry(config)
    routed = table.get(f"worker-{ROUTING.get(kind, 'medium')}")
    if routed is not None and kind in routed["strengths"]:
        return routed
    candidates = [
        item
        for item in table.values()
        if item["name"] != FALLBACK_AGENT and kind in item["strengths"]
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


def route(
    kind: str,
    capability_row: dict[str, Any],
    level: str | None = None,
) -> dict[str, Any]:
    """Nivel, variante y steps para un tipo, sin escalar ni inventar niveles."""
    from .domain import ValidationError
    from .util import check_enum

    check_enum(kind, KINDS, "tipo")
    target = (level or ROUTING.get(kind, "medium")).strip().lower()
    if target not in VARIANT_LEVELS:
        raise ValidationError(
            f"Nivel desconocido: {level!r}; usa {', '.join(VARIANT_LEVELS)}"
        )
    supported = capability_row.get("variants") or []
    chosen = target if target in supported else None
    return {
        "level": target,
        "variant": chosen,
        "model": capability_row.get("model"),
        "steps": capability_row.get("steps", STEPS_BY_LEVEL[target]),
        "fallback": chosen is None,
    }


def assign(
    kind: str, config: dict[str, Any] | None = None
) -> dict[str, Any]:
    """Plan lanzable: perfil existente + modelo, variante, steps y coste."""
    level = ROUTING.get(kind, "medium")
    table = registry(config)
    profile = f"worker-{level}"
    if profile not in table or kind not in table[profile]["strengths"]:
        profile = FALLBACK_AGENT
    selected = table[profile]
    plan = route(kind, selected)
    return {
        "agent": selected["name"],
        "kind": kind,
        "model": selected["model"],
        "variant": selected["variant"],
        "level": plan["level"],
        "steps": selected["steps"],
        "fallback": plan["fallback"],
        "max_parallel": selected["max_parallel"],
        "cost": selected["cost"],
    }


def concurrency_budget(config: dict[str, Any] | None = None) -> int:
    """Writers simultáneos permitidos: ``orchestrator.max_parallel`` o 3."""
    from .domain import ValidationError

    raw = (config or {}).get("orchestrator")
    value = (
        raw.get("max_parallel", DEFAULT_CONCURRENCY)
        if isinstance(raw, dict)
        else DEFAULT_CONCURRENCY
    )
    if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= 8:
        raise ValidationError(
            "orchestrator.max_parallel debe ser un entero entre 1 y 8"
        )
    return value
