"""Configuración del bloque ``memory.*`` con defaults y validación estricta."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from ..domain import ValidationError
from ..workspace import load_config
from .models import MEMORY_SCOPES, WRITE_POLICIES

DEFAULT_MEMORY_CONFIG: dict[str, Any] = {
    "enabled": True,
    "write_policy": "balanced",
    "project_budget_chars": 3000,
    "user_budget_chars": 1200,
    "brief_budget_chars": 4500,
    "max_entry_chars": 600,
    "max_playbook_chars": 12000,
    "stale_after_days": 45,
    "archive_after_days": 120,
    "candidate_min_evidence": 2,
    "candidate_lookback_days": 90,
    "instructions_sync": True,
    "skills_sync": True,
    "user_scope": "project",
}

_BOOL_KEYS = (
    "enabled",
    "instructions_sync",
    "skills_sync",
)
_POSITIVE_KEYS = (
    "project_budget_chars",
    "user_budget_chars",
    "brief_budget_chars",
    "max_entry_chars",
    "max_playbook_chars",
    "candidate_min_evidence",
    "candidate_lookback_days",
)
_NON_NEGATIVE_KEYS = ("stale_after_days", "archive_after_days")


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


__all__ = [
    "DEFAULT_MEMORY_CONFIG",
    "load_memory_config",
    "validate_memory_config",
]
