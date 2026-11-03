"""Constantes, estados y errores del dominio de tickets."""

from __future__ import annotations

import fnmatch
import json
import re
from pathlib import Path
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
    # Commit de otro repositorio (por ejemplo, dotfiles); ``ref`` es
    # ``repo@sha`` y lo crea ``taiki commit --repo <ruta>``.
    "external",
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


# Rutas de sistema cuyo cambio exige autorización explícita del usuario.
HIGH_RISK_PATHS = ("/etc", "/usr", "/boot", "/var/lib")
HIGH_RISK_RESOURCES = ("systemd:system/", "package:")
MEDIUM_RISK_RESOURCES = ("systemd:user/",)
MEDIUM_RISK_PATHS = ("~/.config", "~/.local")


def _under_prefix(path: str, prefix: str) -> bool:
    target = str(Path(path).expanduser())
    root = str(Path(prefix).expanduser())
    return target == root or target.startswith(root.rstrip("/") + "/")


# Rutas del propio taiki (guard, agentes, dominio, gates). Un cambio ahí exige
# una revisión explícita del usuario antes de pasar a ``verificado``.
SENSITIVE_PATHS = (
    "ticketlib/guard/",
    ".opencode/agents/",
    "tests/budget.toml",
    "ticketlib/domain.py",
    "ticketlib/store/workflow.py",
    "tests/coverage_gate.py",
    "tests/benchmarks.py",
)


def _sensitive_match(path: str, sensitive: str) -> bool:
    path = path.strip().replace("\\", "/")
    while path.startswith("./"):
        path = path[2:]
    if not path:
        return False
    if sensitive.endswith("/"):
        root = sensitive.rstrip("/")
        return (
            path == root
            or path.startswith(sensitive)
            or fnmatch.fnmatch(root, path)
        )
    return (
        path == sensitive
        or fnmatch.fnmatch(path, sensitive)
        or fnmatch.fnmatch(sensitive, path)
    )


def requires_review(paths: Any) -> bool:
    """¿El ticket posee rutas que exigen la revisión especial del usuario?

    Aviso, no gate: marca ``requires_review`` en el ticket para que el usuario
    sepa que toca rutas sensibles, pero **no bloquea** ``transition`` a
    ``verificado``. ``taiki review`` es opcional y solo deja constancia; la
    revisión especial es ``taiki confirm``, exclusiva del usuario.
    """
    return any(
        _sensitive_match(str(item), sensitive)
        for item in (paths or [])
        if str(item).strip()
        for sensitive in SENSITIVE_PATHS
    )


def risk_for(paths: Any, resources: Any) -> str:
    """Riesgo derivado del alcance: ``high``, ``medium`` o ``low``.

    El riesgo no se declara a mano: se deduce de los paths y recursos del
    ticket. ``high`` toca el sistema (systemd de sistema, paquetes o rutas
    como ``/etc``); ``medium`` la configuración del usuario.
    """
    path_items = [str(item) for item in (paths or []) if str(item).strip()]
    resource_items = [str(item) for item in (resources or []) if str(item).strip()]
    if any(
        item.startswith(HIGH_RISK_RESOURCES) for item in resource_items
    ) or any(
        _under_prefix(item, root)
        for item in path_items
        for root in HIGH_RISK_PATHS
    ):
        return "high"
    if any(
        item.startswith(MEDIUM_RISK_RESOURCES) for item in resource_items
    ) or any(
        _under_prefix(item, root)
        for item in path_items
        for root in MEDIUM_RISK_PATHS
    ):
        return "medium"
    return "low"


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
