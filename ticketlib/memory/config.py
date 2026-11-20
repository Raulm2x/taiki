"""Configuración del bloque ``memory.*`` con defaults y validación estricta."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from ..domain import ValidationError
from ..workspace import load_config
from .models import MEMORY_SCOPES, TAG_RE, WRITE_POLICIES

DEFAULT_AUTO_CURATE: dict[str, Any] = {
    "enabled": False,
    "interval_hours": 24,
    "idle_minutes": 0,
}

DEFAULT_MEMORY_CONFIG: dict[str, Any] = {
    "enabled": True,
    "write_policy": "balanced",
    "project_budget_chars": 3000,
    "user_budget_chars": 1200,
    "brief_budget_chars": 4500,
    "brief_mode": "full",
    "layer0_tags": ["security", "seguridad", "scope", "sudo", "confirm"],
    "enforce_budget": False,
    "max_entry_chars": 600,
    "max_playbook_chars": 12000,
    "playbook_index_chars": 400,
    "stale_after_days": 45,
    "archive_after_days": 120,
    "candidate_min_evidence": 2,
    "candidate_lookback_days": 90,
    "candidate_routing_min_samples": 5,
    "candidate_max": 20,
    "candidate_similarity": 0.8,
    "instructions_sync": True,
    "skills_sync": True,
    "auto_approve_evidence": True,
    "user_scope": "project",
    "auto_curate": dict(DEFAULT_AUTO_CURATE),
    "review_command": None,
    "review_timeout_seconds": 15,
}

_BOOL_KEYS = (
    "enabled",
    "enforce_budget",
    "instructions_sync",
    "skills_sync",
    "auto_approve_evidence",
)
_BRIEF_MODES = ("full", "index", "grouped")
_POSITIVE_KEYS = (
    "project_budget_chars",
    "user_budget_chars",
    "brief_budget_chars",
    "max_entry_chars",
    "max_playbook_chars",
    "playbook_index_chars",
    "candidate_min_evidence",
    "candidate_lookback_days",
    "candidate_routing_min_samples",
    "candidate_max",
    "review_timeout_seconds",
)
_NON_NEGATIVE_KEYS = ("stale_after_days", "archive_after_days")
# Umbrales de similitud: proporción abierta por ambos extremos (0 < x <= 1).
_SIMILARITY_KEYS = ("candidate_similarity",)


def _fail(key: str, expected: str, value: object) -> None:
    raise ValidationError(
        f"Configuración memory.{key} inválida: {value!r} (esperado {expected})"
    )


def validate_memory_config(raw: Any) -> dict[str, Any]:
    """Valida el bloque ``memory`` y lo completa con los defaults.

    Las claves desconocidas fallan: un error tipográfico en la configuración
    debe verse al arrancar, no descubrirse cuando el brief ignora un ajuste.
    """
    if raw is None:
        raw = {}
    if not isinstance(raw, dict):
        raise ValidationError(
            "La configuración 'memory' debe ser un objeto JSON con claves conocidas"
        )
    unknown = sorted(set(raw) - set(DEFAULT_MEMORY_CONFIG))
    if unknown:
        raise ValidationError(
            "Claves desconocidas en memory.*: "
            f"{', '.join(unknown)}. "
            f"Permitidas: {', '.join(sorted(DEFAULT_MEMORY_CONFIG))}"
        )

    result = dict(DEFAULT_MEMORY_CONFIG)
    result.update(raw)

    for key in _BOOL_KEYS:
        if not isinstance(result[key], bool):
            _fail(key, "true o false", result[key])

    for key in _POSITIVE_KEYS:
        value = result[key]
        if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
            _fail(key, "un entero mayor que 0", value)

    for key in _NON_NEGATIVE_KEYS:
        value = result[key]
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            _fail(key, "un entero mayor o igual que 0", value)

    for key in _SIMILARITY_KEYS:
        value = result[key]
        if (
            isinstance(value, bool)
            or not isinstance(value, (int, float))
            or not 0 < float(value) <= 1
        ):
            _fail(key, "un número mayor que 0 y menor o igual que 1", value)
        result[key] = float(value)

    auto = result["auto_curate"]
    if not isinstance(auto, dict):
        _fail("auto_curate", "un objeto {enabled, interval_hours, idle_minutes}", auto)
    unknown_auto = sorted(set(auto) - set(DEFAULT_AUTO_CURATE))
    if unknown_auto:
        _fail(
            "auto_curate",
            f"claves {', '.join(sorted(DEFAULT_AUTO_CURATE))}",
            auto,
        )
    auto_result = dict(DEFAULT_AUTO_CURATE)
    auto_result.update(auto)
    if not isinstance(auto_result["enabled"], bool):
        _fail("auto_curate.enabled", "true o false", auto_result["enabled"])
    interval = auto_result["interval_hours"]
    if isinstance(interval, bool) or not isinstance(interval, int) or interval <= 0:
        _fail("auto_curate.interval_hours", "un entero mayor que 0", interval)
    idle = auto_result["idle_minutes"]
    if isinstance(idle, bool) or not isinstance(idle, int) or idle < 0:
        _fail("auto_curate.idle_minutes", "un entero mayor o igual que 0", idle)
    result["auto_curate"] = auto_result

    command = result["review_command"]
    if command is not None:
        if not isinstance(command, str):
            _fail("review_command", "un texto o null", command)
        command = command.strip()
        result["review_command"] = command or None

    if result["max_playbook_chars"] < result["max_entry_chars"]:
        _fail(
            "max_playbook_chars",
            "un valor mayor o igual que max_entry_chars",
            result["max_playbook_chars"],
        )

    if (
        result["archive_after_days"]
        and result["stale_after_days"]
        and result["archive_after_days"] < result["stale_after_days"]
    ):
        _fail(
            "archive_after_days",
            "un valor mayor o igual que stale_after_days",
            result["archive_after_days"],
        )

    if result["write_policy"] not in WRITE_POLICIES:
        _fail("write_policy", f"uno de {', '.join(WRITE_POLICIES)}", result["write_policy"])

    if result["user_scope"] not in MEMORY_SCOPES:
        _fail("user_scope", f"uno de {', '.join(MEMORY_SCOPES)}", result["user_scope"])

    if result["brief_mode"] not in _BRIEF_MODES:
        _fail("brief_mode", f"uno de {', '.join(_BRIEF_MODES)}", result["brief_mode"])

    layer0 = result["layer0_tags"]
    if not isinstance(layer0, list) or not all(
        isinstance(item, str) for item in layer0
    ):
        _fail("layer0_tags", "una lista de textos", layer0)
    normalized: list[str] = []
    for item in layer0:
        value = item.strip().lower()
        if not value:
            continue
        if not TAG_RE.fullmatch(value):
            _fail(
                "layer0_tags",
                "tags en minúsculas (1-33 caracteres: a-z 0-9 . _ / -)",
                item,
            )
        normalized.append(value)
    result["layer0_tags"] = normalized

    return result


def load_memory_config(
    workspace: str | Path | None = None,
    config: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Carga y valida ``memory`` del workspace o de un config ya leído."""
    data = config if config is not None else load_config(workspace)
    if not isinstance(data, dict):
        data = {}
    return validate_memory_config(data.get("memory"))


def resolve_memory_config(value: dict[str, Any] | None) -> dict[str, Any]:
    """Acepta None, el bloque ``memory`` o el config raíz completo.

    Regla única para todos los flujos de memoria: un dict con ``memory`` usa
    ese bloque; un dict cuyas claves son todas del bloque ``memory`` se valida
    tal cual (estricto); cualquier otro dict es un config raíz y se lee su
    ``memory`` (ausente → defaults del workspace). Así ``schedule`` o
    ``agents`` pueden pasar el config raíz sin bloque ``memory`` sin que las
    claves ajenas (``project``, ``guard``, ``scope``…) se confundan con
    claves desconocidas de ``memory.*``.
    """
    if value is None:
        return load_memory_config()
    if not isinstance(value, dict):
        return validate_memory_config(value)
    if "memory" in value:
        return validate_memory_config(value.get("memory"))
    if set(value) <= set(DEFAULT_MEMORY_CONFIG):
        return validate_memory_config(value)
    return load_memory_config(config=value)


__all__ = [
    "DEFAULT_AUTO_CURATE",
    "DEFAULT_MEMORY_CONFIG",
    "load_memory_config",
    "resolve_memory_config",
    "validate_memory_config",
]
