"""Guard de tickets: ningún cambio sin un ticket activo.

El guard responde una sola pregunta: ¿puede esta sesión de agente escribir en
disco? Si no tiene un ticket ``en_progreso``, reutiliza el ticket pendiente que
haya creado la propia sesión o crea uno con el título de la sesión, lo reclama
y bloquea la primera acción con un mensaje accionable. El segundo intento ya
pasa, así que el agente nunca se queda atascado.
"""

from __future__ import annotations

from .commands import (
    EXEMPT_ROOTS,
    HOME,
    command_is_mutating,
    command_touches_home,
    path_guarded,
    risky_git,
    split_commands,
)
from .context import (
    auto_title,
    find_active,
    find_pending,
    find_session_epic,
    session_chain,
)
from .hook import is_mutating, normalize_tool, parse_hook_input
from .policy import auto_mode, disabled, evaluate

__all__ = [
    "command_is_mutating",
    "command_touches_home",
    "risky_git",
    "disabled",
    "auto_mode",
    "auto_title",
    "evaluate",
    "find_active",
    "find_pending",
    "find_session_epic",
    "is_mutating",
    "normalize_tool",
    "parse_hook_input",
    "path_guarded",
    "session_chain",
    "split_commands",
]
