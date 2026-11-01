"""Sesiones de agente: backends, actividad y apertura de terminal.

La existencia de una sesión se comprueba en la base de datos de OpenCode
(``session_v2``). Si la base no existe o está bloqueada el módulo degrada a
"sin sesión": los tickets se muestran en gris y sin enlace, nunca fallan.
"""

from __future__ import annotations

from ..agents import (
    AGENT_PALETTE,
    NO_SESSION_COLOR,
    agent_color,
    agent_name,
)
from .activity import decorate, ticket_activity, ticket_sessions
from .backends import (
    ANTIGRAVITY_APP_DATA_DIR_ENV,
    OPENCODE_DB_ENV,
    antigravity_brain_path,
    existing_sessions,
    opencode_db_path,
    session_info,
    session_parent,
)
from .terminals import (
    DEFAULT_TERMINAL,
    GENERIC_TERMINAL,
    LAUNCH_PROBE,
    TERMINAL_ENV,
    DirectoryChoice,
    SessionError,
    TerminalSpec,
    agent_executable,
    build_open_command,
    detect_backend,
    ensure_session,
    launch_session,
    resolve_directory,
    terminal_spec,
)

__all__ = [
    "AGENT_PALETTE",
    "ANTIGRAVITY_APP_DATA_DIR_ENV",
    "DEFAULT_TERMINAL",
    "GENERIC_TERMINAL",
    "DirectoryChoice",
    "LAUNCH_PROBE",
    "NO_SESSION_COLOR",
    "OPENCODE_DB_ENV",
    "SessionError",
    "TERMINAL_ENV",
    "TerminalSpec",
    "agent_color",
    "agent_executable",
    "agent_name",
    "antigravity_brain_path",
    "build_open_command",
    "decorate",
    "detect_backend",
    "ensure_session",
    "existing_sessions",
    "launch_session",
    "opencode_db_path",
    "resolve_directory",
    "session_info",
    "session_parent",
    "terminal_spec",
    "ticket_activity",
    "ticket_sessions",
]
