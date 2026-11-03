"""Constantes, estados y errores del dominio de tickets."""

from __future__ import annotations

import json
import re
from typing import Any

STATUSES = (
    "nuevo",
    "pendiente",
    "en_progreso",
    "completado",
    "verificado",
    "confirmado",
    "bloqueado",
    "cancelado",
)

# ``verificado`` no está abierto: el trabajo terminó y espera al usuario. Tampoco
# es final: el cierre real lo marca ``confirmado``.
OPEN_STATUSES = ("nuevo", "pendiente", "en_progreso", "bloqueado")
FINAL_STATUSES = ("confirmado", "cancelado")
# Una épica puede cerrarse cuando sus tareas están en alguno de estos estados.
PARENT_DONE_STATUSES = ("completado", "verificado", "confirmado", "cancelado")
PARENT_CONFIRMED_STATUSES = ("confirmado", "cancelado")
# Una dependencia solo se considera cerrada cuando el conductor la ha
# validado (verificado) o el usuario la ha confirmado. ``completado`` aún
# puede fallar la verificación y no debe desbloquear trabajo derivado.
DEPENDENCY_DONE_STATUSES = ("verificado", "confirmado", "cancelado")

KINDS = (
    "mantenimiento",
    "funcionalidad",
    "bug",
    "documentacion",
    "seguridad",
    "otro",
)

PRIORITIES = ("baja", "media", "alta", "urgente")
PRIORITY_RANK = {"urgente": 0, "alta": 1, "media": 2, "baja": 3}

LINK_KINDS = (
    "commit",
    "archivo",
    "url",
    "sesion",
    "worktree",
    "rama",
    "depende",
    "relacionado",
    "otro",
)
# Enlace que expresa "este ticket depende de otro": el ref es un código T-XXXX.
DEPENDENCY_KIND = "depende"
ACTOR_TYPES = ("agente", "usuario")

TRANSITIONS: dict[str, set[str]] = {
    "nuevo": {"pendiente", "en_progreso", "cancelado"},
    "pendiente": {"en_progreso", "bloqueado", "cancelado"},
    "en_progreso": {"completado", "bloqueado", "pendiente", "cancelado"},
    "bloqueado": {"pendiente", "en_progreso", "cancelado"},
    # ``verificado`` es el visto bueno del conductor; el usuario puede
    # confirmar tanto desde ``completado`` como desde ``verificado``.
    "completado": {"verificado", "confirmado", "en_progreso", "bloqueado"},
    "verificado": {"confirmado", "en_progreso", "bloqueado"},
    "confirmado": {"en_progreso"},
    "cancelado": {"pendiente"},
}

REOPEN_FROM = {"completado", "verificado", "confirmado", "cancelado"}

SCHEMA_VERSION = 7

# Aislamiento del trabajo por ticket. ``worktree`` es el default
# retrocompatible; ``none`` trabaja en el árbol vivo de forma serial y ``scope``
# limita los cambios a ``paths`` + ``resources``.
ISOLATION_MODES = ("none", "scope", "worktree")
DEFAULT_ISOLATION = "worktree"
MAX_RESOURCES = 50
RESOURCE_PREFIXES = (
    "systemd:user/",
    "systemd:system/",
    "package:",
    "service:",
)
REVERSAL_FIELDS = ("kind", "ref", "command")
_RESOURCE_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9@._:+/-]*$")
_BRANCH_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/-]*$")


def clean_isolation(value: Any, default: str = DEFAULT_ISOLATION) -> str:
    """Valida el modo de aislamiento; ``None``/vacío usa el default."""
    if value is None or str(value).strip() == "":
        return default
    mode = str(value).strip().lower()
    if mode not in ISOLATION_MODES:
        raise ValidationError(
            f"Aislamiento inválido: {value!r} "
            f"(esperado uno de {', '.join(ISOLATION_MODES)})"
        )
    return mode


def clean_resources(resources: Any) -> list[str]:
    """Normaliza y valida recursos tipados, sin duplicados."""
    if resources is None:
        return []
    if isinstance(resources, str):
        items = [resources]
    elif isinstance(resources, (list, tuple, set)):
        items = list(resources)
    else:
        raise ValidationError("Los recursos deben ser una lista de cadenas")
    cleaned: list[str] = []
    for raw in items:
        if not isinstance(raw, str):
            raise ValidationError(f"Recurso inválido: {raw!r}")
        item = raw.strip()
        if not item:
            raise ValidationError("Un recurso no puede quedar vacío")
        prefix = next(
            (candidate for candidate in RESOURCE_PREFIXES if item.startswith(candidate)),
            None,
        )
        if prefix is None:
            raise ValidationError(
                f"Recurso no soportado: {item!r} "
                "(usa systemd:user/<unit>, systemd:system/<unit>, "
                "package:<name> o service:<name>)"
            )
        name = item[len(prefix):]
        if not name or not _RESOURCE_NAME.fullmatch(name):
            raise ValidationError(f"Recurso inválido: {item!r}")
        if item in cleaned:
            raise ValidationError(f"Recurso duplicado: {item}")
        cleaned.append(item)
    if len(cleaned) > MAX_RESOURCES:
        raise ValidationError(
            f"Demasiados recursos ({len(cleaned)}); máximo {MAX_RESOURCES}"
        )
    return cleaned


def clean_reversal(reversal: Any) -> dict[str, str] | None:
    """Valida la reversión ``{kind, ref, command}``; admite JSON o dict."""
    if reversal is None or (isinstance(reversal, str) and not reversal.strip()):
        return None
    data = reversal
    if isinstance(reversal, str):
        try:
            data = json.loads(reversal)
        except json.JSONDecodeError as exc:
            raise ValidationError(
                f"Reversión inválida: JSON no parseable ({exc.msg})"
            ) from exc
    if not isinstance(data, dict):
        raise ValidationError(
            "La reversión debe ser un objeto {kind, ref, command}"
        )
    unknown = set(data) - set(REVERSAL_FIELDS)
    if unknown:
        raise ValidationError(
            "Campos de reversión no soportados: " + ", ".join(sorted(unknown))
        )
    kind = str(data.get("kind") or "").strip()
    if not kind:
        raise ValidationError("La reversión necesita 'kind'")
    cleaned = {"kind": kind}
    for key in ("ref", "command"):
        value = data.get(key)
        if value is None:
            continue
        text = str(value).strip()
        if text:
            cleaned[key] = text
    return cleaned


def clean_branch(value: Any, default: str = "main") -> str:
    """Valida el nombre de la rama live configurada."""
    if value is None or str(value).strip() == "":
        return default
    branch = str(value).strip()
    if not _BRANCH_NAME.fullmatch(branch) or ".." in branch:
        raise ValidationError(f"Rama inválida: {value!r}")
    return branch

# Marcador interno de los tickets temporales (scratch). Lleva guion bajo para
# no colisionar con etiquetas de tema del usuario (p. ej. tags=["scratch"]).
SCRATCH_TAG = "_scratch"
SCRATCH_SESSION_PREFIX = "_scratch-sesion:"
# Los temporales tienen su propio espacio de códigos: así no consumen números
# T-XXXX y la secuencia visible queda sin huecos.
SCRATCH_CODE_PREFIX = "S-"
SCRATCH_COUNTER = "next_scratch_number"
# Etiqueta de los tickets genéricos que cubren huecos históricos.
TOMBSTONE_TAG = "hueco"
TICKET_COUNTER = "next_ticket_number"


class TicketError(Exception):
    """Error de dominio del sistema de tickets."""


class ValidationError(TicketError):
    """Datos de entrada inválidos."""


class NotFoundError(TicketError):
    """Ticket inexistente."""


class TransitionError(TicketError):
    """Transición de estado no permitida."""


class PermissionDenied(TicketError):
    """Acción reservada al usuario."""


class StaleServerError(TicketError):
    """El MCP en ejecución no corresponde al código en disco."""
