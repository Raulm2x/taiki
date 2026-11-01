"""Acciones de administración de la TUI, sin curses.

Cada función abre y cierra su propia conexión para que la vista no tenga que
saber nada de SQLite. Los errores de dominio se propagan tal cual para que la
TUI los muestre como aviso.
"""

from __future__ import annotations

import base64
import shutil
import subprocess
import sys
from typing import Any

from ticketlib import db, repo
from ticketlib.domain import REOPEN_FROM, TRANSITIONS

CONFIRM_ONLY = "confirmado"


def _connect():
    return db.connect()


def claim(code: str, agent: str, session_id: str | None = None) -> dict[str, Any]:
    """Reclama el ticket para ``agent`` (pasa a en_progreso)."""
    conn = _connect()
    try:
        return repo.claim_ticket(conn, code, agent=agent, session_id=session_id)
    finally:
        conn.close()


def assign(code: str, agent: str, actor: str) -> dict[str, Any]:
    """Asigna el ticket sin cambiar de estado."""
    conn = _connect()
    try:
        return repo.update_ticket(
            conn, code, assigned_to=agent, actor=actor
        )
    finally:
        conn.close()


def change_status(
    code: str,
    status: str,
    note: str | None = None,
    actor: str = "usuario",
    session_id: str | None = None,
) -> dict[str, Any]:
    """Aplica una transición validada por el dominio."""
    conn = _connect()
    try:
        return repo.transition(
            conn,
            code,
            status,
            note=note,
            actor=actor,
            actor_type="usuario",
            session_id=session_id,
        )
    finally:
        conn.close()


def comment(
    code: str,
    body: str,
    author: str = "usuario",
    session_id: str | None = None,
) -> dict[str, Any]:
    """Añade un comentario al historial."""
    conn = _connect()
    try:
        return repo.comment_ticket(
            conn,
            code,
            body,
            author=author,
            actor_type="usuario",
            session_id=session_id,
        )
    finally:
        conn.close()


def link_commit(code: str, ref: str, actor: str = "usuario") -> dict[str, Any]:
    """Enlaza un commit al ticket."""
    conn = _connect()
    try:
        return repo.link_ticket(conn, code, "commit", ref, actor=actor)
    finally:
        conn.close()


def available_statuses(ticket: dict[str, Any]) -> list[str]:
    """Estados alcanzables desde el actual, sin 'confirmado' (usa ``c``)."""
    current = ticket["status"]
    allowed = sorted(TRANSITIONS.get(current, set()))
    return [status for status in allowed if status != CONFIRM_ONLY]


def needs_note(ticket: dict[str, Any], status: str) -> bool:
    """Reabrir exige motivo."""
    return status in ("en_progreso", "pendiente") and ticket["status"] in REOPEN_FROM


def osc52(text: str) -> str:
    """Secuencia OSC 52 para copiar al portapapeles del terminal."""
    payload = base64.b64encode(text.encode("utf-8")).decode("ascii")
    return f"\033]52;c;{payload}\033\\"


def copy_text(text: str) -> str | None:
    """Copia ``text`` y devuelve el método usado, o ``None`` si falló."""
    commands = (
        ("wl-copy", ["wl-copy"]),
        ("xclip", ["xclip", "-selection", "clipboard"]),
        ("pbcopy", ["pbcopy"]),
    )
    for name, argv in commands:
        if not shutil.which(argv[0]):
            continue
        try:
            subprocess.run(
                argv, input=text.encode("utf-8"), check=True, timeout=3
            )
        except (OSError, subprocess.SubprocessError):
            continue
        return name
    try:
        sys.stdout.write(osc52(text))
        sys.stdout.flush()
    except OSError:
        return None
    return "osc52"
