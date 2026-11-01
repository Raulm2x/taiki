"""Utilidades compartidas por los comandos del CLI."""

from __future__ import annotations

import sys
from contextlib import contextmanager

from ticketlib import db, render


@contextmanager
def _conn():
    conn = db.connect()
    try:
        yield conn
    finally:
        conn.close()


def _show(ticket: dict, as_json: bool, session_id: str | None = None) -> None:
    """Imprime un ticket en JSON o en formato detallado."""
    if as_json:
        print(render.to_json({**ticket, "session_id": session_id}))
    else:
        print(render.render_detail(ticket, session_id=session_id))


def _read_body(value: str | None, prompt: str) -> str:
    """Devuelve el cuerpo dado o lo pide por stdin si es interactivo."""
    if value is not None:
        return value
    if sys.stdin.isatty():
        return input(prompt).strip()
    return ""
