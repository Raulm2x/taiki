"""Constantes, estados y errores del dominio de tickets."""

from __future__ import annotations

STATUSES = (
    "nuevo",
    "pendiente",
    "en_progreso",
    "completado",
    "confirmado",
    "bloqueado",
    "cancelado",
)

OPEN_STATUSES = ("nuevo", "pendiente", "en_progreso", "bloqueado")
FINAL_STATUSES = ("confirmado", "cancelado")
# Una épica puede cerrarse cuando sus tareas están en alguno de estos estados.
PARENT_DONE_STATUSES = ("completado", "confirmado", "cancelado")
PARENT_CONFIRMED_STATUSES = ("confirmado", "cancelado")

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

LINK_KINDS = ("commit", "archivo", "url", "sesion", "otro")
ACTOR_TYPES = ("agente", "usuario")

TRANSITIONS: dict[str, set[str]] = {
    "nuevo": {"pendiente", "en_progreso", "cancelado"},
    "pendiente": {"en_progreso", "bloqueado", "cancelado"},
    "en_progreso": {"completado", "bloqueado", "pendiente", "cancelado"},
    "bloqueado": {"pendiente", "en_progreso", "cancelado"},
    "completado": {"confirmado", "en_progreso"},
    "confirmado": {"en_progreso"},
    "cancelado": {"pendiente"},
}

REOPEN_FROM = {"completado", "confirmado", "cancelado"}

SCHEMA_VERSION = 1

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
