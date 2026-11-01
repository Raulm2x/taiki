"""Utilidades compartidas por los comandos del CLI."""

from __future__ import annotations

import sys
from contextlib import contextmanager

from ticketlib import db, render, scratch


@contextmanager
def _conn():
    conn = db.connect()
    try:
        yield conn
    finally:
        conn.close()


def _clean_expired(conn) -> list[str]:
    """Archiva scratch caducados antes de una vista (limpieza perezosa)."""
    try:
        return scratch.clean(conn)
    except Exception:  # noqa: BLE001 - una vista nunca debe fallar por esto
        return []


def _show(
    ticket: dict,
    as_json: bool,
    session_id: str | None = None,
    *,
    compact: bool = False,
) -> None:
    """Imprime un ticket en JSON o en formato detallado."""
    if as_json:
        print(
            render.to_json(
                {**ticket, "session_id": session_id}, compact=compact
            )
        )
    else:
        print(render.render_detail(ticket, session_id=session_id))


def _read_body(value: str | None, prompt: str) -> str:
    """Devuelve el cuerpo dado o lo pide por stdin si es interactivo."""
    if value is not None:
        return value
    if sys.stdin.isatty():
        return input(prompt).strip()
    return ""
