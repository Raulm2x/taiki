"""Validación de argumentos MCP contra el ``inputSchema`` de cada herramienta.

Los clientes no están obligados a validar el esquema: si un agente escribe
``statuss`` en vez de ``statuses``, el servidor debe rechazarlo en vez de
ignorarlo y devolver resultados engañosos.
"""

from __future__ import annotations

from typing import Any

from ticketlib.domain import ValidationError

from . import schemas

PREFIXES = ("taiki_", "tickets_")


def tool_schema(name: str) -> dict[str, Any] | None:
    """Esquema de la herramienta, aceptando los alias con prefijo."""
    base = str(name or "")
    for prefix in PREFIXES:
        if base.startswith(prefix):
            base = base.removeprefix(prefix)
            break
    for tool in schemas.TOOLS:
        if tool["name"] == base:
            return tool
    return None


def _type_ok(value: Any, expected: Any) -> bool:
    if isinstance(expected, list):
        return any(_type_ok(value, item) for item in expected)
    if expected == "null":
        return value is None
    if expected == "string":
        return isinstance(value, str)
    if expected == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if expected == "number":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    if expected == "boolean":
        return isinstance(value, bool)
    if expected == "array":
        return isinstance(value, list)
    if expected == "object":
        return isinstance(value, dict)
    return True


def _check_value(name: str, value: Any, spec: dict[str, Any]) -> None:
    expected = spec.get("type")
    if expected and not _type_ok(value, expected):
        label = expected if isinstance(expected, str) else " o ".join(expected)
        raise ValidationError(
            f"El argumento '{name}' debe ser {label}, no {type(value).__name__}"
        )
    allowed = spec.get("enum")
    if allowed is not None and value not in allowed:
        values = ", ".join(str(item) for item in allowed)
        raise ValidationError(
            f"Valor no permitido para '{name}': {value!r}. Válidos: {values}"
        )
    if isinstance(value, str) and spec.get("maxLength") is not None:
        limit = int(spec["maxLength"])
        if len(value) > limit:
            raise ValidationError(
                f"'{name}' supera el máximo de {limit} caracteres"
            )
    if isinstance(value, list) and spec.get("maxItems") is not None:
        limit = int(spec["maxItems"])
        if len(value) > limit:
            raise ValidationError(
                f"'{name}' supera el máximo de {limit} elementos"
            )
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        minimum = spec.get("minimum")
        maximum = spec.get("maximum")
        if minimum is not None and value < minimum:
            raise ValidationError(
                f"'{name}' debe ser mayor o igual que {minimum}"
            )
        if maximum is not None and value > maximum:
            raise ValidationError(
                f"'{name}' debe ser menor o igual que {maximum}"
            )
    if isinstance(value, list) and isinstance(spec.get("items"), dict):
        for index, item in enumerate(value):
            _check_value(f"{name}[{index}]", item, spec["items"])


def validate_arguments(name: str, args: Any) -> None:
    """Valida ``args`` y lanza ``ValidationError`` con un mensaje accionable."""
    schema = tool_schema(name)
    if schema is None:
        return
    if args is None:
        args = {}
    if not isinstance(args, dict):
        raise ValidationError("Los argumentos deben ser un objeto JSON")

    spec = schema.get("inputSchema") or {}
    properties = spec.get("properties") or {}
    if spec.get("additionalProperties") is False:
        unknown = sorted(set(args) - set(properties))
        if unknown:
            raise ValidationError(
                "Argumento(s) desconocido(s): "
                + ", ".join(unknown)
                + ". Válidos: "
                + ", ".join(sorted(properties))
            )
    for key in spec.get("required") or ():
        if key not in args or args[key] is None:
            raise ValidationError(f"Falta el argumento obligatorio '{key}'")
    for key, value in args.items():
        if key in properties:
            _check_value(key, value, properties[key])


__all__ = ["tool_schema", "validate_arguments"]
