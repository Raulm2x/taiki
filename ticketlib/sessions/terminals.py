"""Terminales, directorios y lanzamiento de sesiones."""

from __future__ import annotations

import os
import shutil
import sqlite3
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

from .backends import _connect_ro, antigravity_brain_path, session_info

DEFAULT_TERMINAL = "kitty"
TERMINAL_ENV = "TICKET_TERMINAL"

LAUNCH_PROBE = 0.5

class SessionError(Exception):
    """No se pudo abrir la sesión solicitada."""

@dataclass(frozen=True)
class TerminalSpec:
    """Cómo pide cada terminal el directorio y cómo ejecuta un comando.

    ``dir_args`` se emite antes de la ruta y ``exec_args`` justo antes del
    programa, de modo que ``wezterm`` puede usar ``start --cwd DIR -- prog``.
    """

    dir_args: tuple[str, ...]
    exec_args: tuple[str, ...]

# Terminales conocidas. La clave es el nombre del ejecutable (sin ruta).
TERMINAL_SPECS: dict[str, TerminalSpec] = {
    "kitty": TerminalSpec(("--directory",), ("-e",)),
    "alacritty": TerminalSpec(("--working-directory",), ("-e",)),
    "foot": TerminalSpec(("--working-directory",), ("-e",)),
    "footclient": TerminalSpec(("--working-directory",), ("-e",)),
    "ghostty": TerminalSpec(("--working-directory",), ("-e",)),
    "konsole": TerminalSpec(("--workdir",), ("-e",)),
    "xfce4-terminal": TerminalSpec(("--working-directory",), ("-e",)),
    "gnome-terminal": TerminalSpec(("--working-directory",), ("--",)),
    "wezterm": TerminalSpec(("start", "--cwd"), ("--",)),
}

# Respaldo para terminales no listadas: el flag largo más habitual.
GENERIC_TERMINAL = TerminalSpec(("--working-directory",), ("-e",))

def terminal_spec(name: str, executable: str | None = None) -> TerminalSpec:
    """Especificación de la terminal a partir de su nombre o su ejecutable."""
    for candidate in (Path(executable).name if executable else "", Path(name).name):
        key = candidate.strip().lower()
        if not key:
            continue
        if key in TERMINAL_SPECS:
            return TERMINAL_SPECS[key]
        # ``kitty-git`` o ``foot-1.2`` deben seguir reconociéndose.
        matches = [known for known in TERMINAL_SPECS if key.startswith(known)]
        if matches:
            return TERMINAL_SPECS[max(matches, key=len)]
    return GENERIC_TERMINAL

@dataclass(frozen=True)
class DirectoryChoice:
    """Directorio donde abrir la terminal, su origen y aviso si hubo respaldo."""

    path: str
    source: str
    warning: str | None = None

def _existing_dir(value: str | None) -> Path | None:
    """Ruta si ``value`` es un directorio existente; ``None`` en otro caso."""
    if not value:
        return None
    try:
        path = Path(value).expanduser()
    except (TypeError, ValueError):
        return None
    try:
        return path if path.is_dir() else None
    except OSError:
        return None

def resolve_directory(
    session_directory: str | None,
    ticket_path: str | None = None,
) -> DirectoryChoice:
    """Elige el directorio de apertura validando que exista.

    Prioridad: el directorio de la sesión y, si ya no existe, el del ticket.
    Nunca devuelve una ruta inexistente: si no hay ninguna válida lanza
    ``SessionError`` para que el llamador muestre un error claro.
    """
    session_dir = _existing_dir(session_directory)
    if session_dir is not None:
        return DirectoryChoice(str(session_dir), "sesion")

    ticket_dir = _existing_dir(ticket_path)
    if ticket_dir is not None:
        if session_directory:
            warning = (
                "El directorio de la sesión ya no existe "
                f"({session_directory}); se abre en el del ticket ({ticket_dir})"
            )
        else:
            warning = (
                "La sesión no registra directorio; se abre en el del ticket "
                f"({ticket_dir})"
            )
        return DirectoryChoice(str(ticket_dir), "ticket", warning)

    if session_directory:
        raise SessionError(
            f"El directorio de la sesión ya no existe ({session_directory}) "
            "y el ticket no tiene una ruta válida"
        )
    raise SessionError(
        "La sesión no registra directorio y el ticket no tiene una ruta válida"
    )

AGENT_COMMANDS: dict[str, tuple[str, ...]] = {
    "opencode": ("opencode",),
    "antigravity": ("agy", "antigravity"),
}

def detect_backend(session_id: str) -> str:
    """Backend de la sesión: ``opencode`` o ``antigravity`` (con respaldo)."""
    session_id = (session_id or "").strip()
    if not session_id:
        return "opencode"
    info = session_info([session_id]).get(session_id)
    if info and info.get("backend"):
        return str(info["backend"])
    if (antigravity_brain_path() / session_id).is_dir():
        return "antigravity"
    return "opencode"

def _agent_name(backend: str) -> str:
    """Nombre del ejecutable del agente, prefiriendo el que esté instalado."""
    names = AGENT_COMMANDS.get(backend, AGENT_COMMANDS["opencode"])
    for name in names:
        if shutil.which(name):
            return name
    return names[0]

def agent_executable(backend: str) -> str:
    """Ejecutable del agente instalado; ``SessionError`` si no hay ninguno."""
    names = AGENT_COMMANDS.get(backend, AGENT_COMMANDS["opencode"])
    for name in names:
        if shutil.which(name):
            return name
    raise SessionError(
        "No se encontró el ejecutable del agente ("
        + " / ".join(names)
        + ") en PATH"
    )

def _db_ready() -> bool:
    """``True`` si la base de OpenCode existe y responde a una consulta."""
    conn = _connect_ro()
    if conn is None:
        return False
    try:
        conn.execute("SELECT 1 FROM session_v2 LIMIT 1").fetchall()
        return True
    except sqlite3.Error:
        return False
    finally:
        conn.close()

def ensure_session(session_id: str, backend: str | None = None) -> None:
    """Comprueba que la sesión exista antes de abrirla.

    Abrir una sesión inexistente haría que el agente creara una nueva; aquí se
    corta con un error claro. Si la base de OpenCode no se puede leer se deja
    pasar (modo degradado): mejor intentarlo que bloquear por un fallo ajeno.
    """
    session_id = (session_id or "").strip()
    if not session_id:
        raise SessionError("La sesión es obligatoria")
    if session_info([session_id]).get(session_id):
        return
    if backend == "antigravity":
        raise SessionError(f"La sesión de Antigravity ya no existe: {session_id}")
    if session_id.startswith("ses_") and _db_ready():
        raise SessionError(
            f"La sesión de OpenCode ya no existe ({session_id}): "
            "el ticket apunta a una sesión borrada"
        )

def build_open_command(
    session_id: str,
    directory: str | None = None,
    terminal: str | None = None,
    backend: str | None = None,
) -> list[str]:
    """Construye el argv para abrir la sesión en una terminal nueva.

    Valida la terminal y que el directorio (si se indica) exista de verdad,
    para no lanzar una terminal condenada a fallar.
    """
    session_id = (session_id or "").strip()
    if not session_id:
        raise SessionError("La sesión es obligatoria")

    term = terminal or os.environ.get(TERMINAL_ENV) or DEFAULT_TERMINAL
    executable = shutil.which(term)
    if not executable:
        raise SessionError(f"Terminal no encontrada: {term}")

    workdir: str | None = None
    if directory:
        existing = _existing_dir(directory)
        if existing is None:
            raise SessionError(f"El directorio no existe: {directory}")
        workdir = str(existing)

    backend = backend or detect_backend(session_id)
    spec = terminal_spec(term, executable)
    cmd = [executable]
    if workdir:
        cmd += [*spec.dir_args, workdir]
    cmd += [*spec.exec_args, _agent_name(backend)]
    if backend == "antigravity":
        cmd += ["--conversation", session_id]
    else:
        cmd += ["--session", session_id]

    uwsm = shutil.which("uwsm")
    if uwsm:
        return [uwsm, "app", "--", *cmd]
    return cmd

def launch_session(
    session_id: str,
    directory: str | None = None,
    terminal: str | None = None,
    backend: str | None = None,
    probe: float = LAUNCH_PROBE,
) -> list[str]:
    """Lanza la terminal en segundo plano y devuelve el argv usado.

    Si la terminal muere durante ``probe`` segundos se devuelve un
    ``SessionError`` con su stderr: antes se descartaba en ``DEVNULL`` y el
    clic en AGENTE fallaba en silencio.
    """
    session_id = (session_id or "").strip()
    backend = backend or detect_backend(session_id)
    ensure_session(session_id, backend=backend)
    agent_executable(backend)
    argv = build_open_command(
        session_id, directory=directory, terminal=terminal, backend=backend
    )

    errlog = tempfile.TemporaryFile()
    try:
        proc = subprocess.Popen(
            argv,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=errlog,
            start_new_session=True,
        )
    except OSError as exc:
        errlog.close()
        raise SessionError(f"No se pudo lanzar la terminal: {exc}") from exc

    code: int | None
    try:
        code = proc.wait(timeout=probe) if probe > 0 else None
    except subprocess.TimeoutExpired:
        code = None

    if code is None:
        # Sigue viva: el hijo conserva su propio descriptor de stderr.
        errlog.close()
        return argv

    errlog.seek(0)
    detail = errlog.read().decode("utf-8", "replace").strip()
    errlog.close()
    if code != 0:
        reason = detail.splitlines()[-1] if detail else "sin salida de error"
        raise SessionError(
            f"La terminal terminó con código {code} al abrir la sesión: {reason}"
        )
    return argv
