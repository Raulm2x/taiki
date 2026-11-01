"""Flujos interactivos de la TUI: confirmar, acciones y sesiones."""

from __future__ import annotations

import os
import sqlite3
from typing import Any, Callable

from ticketlib import db, domain, herdr, sessions

from . import actions
from .state import TuiState
from .view import confirm_dialog, menu, prompt

ACTOR = os.environ.get("TICKET_ACTOR", "usuario")

ACTIONS = (
    "Reclamar (pasar a en_progreso)",
    "Asignar a otro agente",
    "Cambiar de estado",
    "Añadir comentario",
    "Copiar código al portapapeles",
    "Enlazar commit",
    "Abrir sesión del agente",
)


def report_text(report: dict[str, Any]) -> tuple[str, str]:
    """Texto y nivel de un informe de confirmación en lote."""
    parts = []
    if report["confirmed"]:
        parts.append("confirmados: " + ", ".join(report["confirmed"]))
    if report["already"]:
        parts.append("ya estaban: " + ", ".join(report["already"]))
    if report["errors"]:
        detail = ", ".join(
            f"{code} ({msg})" for code, msg in report["errors"].items()
        )
        parts.append("errores: " + detail)
    if not parts:
        return "Sin cambios", "info"
    level = "error" if report["errors"] else "ok"
    return " · ".join(parts), level


def confirm_selection(win, state: TuiState) -> bool:
    codes = state.selected_codes()
    if not codes:
        state.notify("No hay tickets marcados para confirmar.", "info")
        return False
    chosen = set(codes)
    rows = sorted(
        (ticket for ticket in state.tickets if ticket["code"] in chosen),
        key=lambda ticket: ticket["code"],
    )
    lines = [f"Confirmar {len(codes)} ticket(s)", ""]
    for ticket in rows:
        lines.append(f"  {ticket['code']}  {ticket['title'][:44]}")
    return confirm_dialog(win, "Confirmar tickets", lines)


def open_session(state: TuiState, ticket: dict[str, Any] | None) -> None:
    if ticket is None:
        return
    session_id = state.session_of(ticket)
    if herdr.available():
        info = (
            sessions.session_info([session_id]).get(session_id, {})
            if session_id
            else {}
        )
        try:
            choice = sessions.resolve_directory(
                info.get("directory"), ticket.get("path")
            )
        except sessions.SessionError as exc:
            state.notify(f"Error: {exc}", "error", ttl=8)
            return
        conn = db.connect()
        try:
            result = herdr.open_and_link(
                conn,
                ticket,
                session_id,
                backend=info.get("backend"),
                directory=choice.path,
                wait=3.0,
            )
        except herdr.HerdrError as exc:
            state.notify(f"Error: {exc}", "error", ttl=8)
            return
        finally:
            conn.close()
        if choice.warning:
            state.notify(choice.warning, "info", ttl=8)
        if result["action"] == "focus":
            state.notify(
                f"{ticket['code']} ya abierto en herdr ({result['pane_id']}).",
                "ok",
            )
        else:
            state.notify(
                f"{ticket['code']} abierto en herdr · {result['pane_id']}", "ok"
            )
        return
    if not session_id:
        state.notify(f"{ticket['code']} no tiene sesión viva.", "info")
        return
    info = sessions.session_info([session_id]).get(session_id, {})
    try:
        choice = sessions.resolve_directory(info.get("directory"), ticket.get("path"))
        sessions.launch_session(
            session_id,
            directory=choice.path,
            backend=info.get("backend"),
            probe=0.35,
        )
    except sessions.SessionError as exc:
        state.notify(f"Error: {exc}", "error", ttl=8)
        return
    if choice.warning:
        state.notify(choice.warning, "info", ttl=8)
    else:
        state.notify(f"Abriendo {ticket['code']} en {choice.path}", "ok")


def _guard(state: TuiState, action: Callable[[], Any], ok_message: str) -> bool:
    try:
        action()
    except (domain.TicketError, sqlite3.Error, OSError) as exc:
        state.notify(f"Error: {exc}", "error")
        return False
    state.notify(ok_message, "ok")
    return True


def _assign(win, state: TuiState, ticket: dict[str, Any]) -> bool:
    current = ticket.get("assigned_to") or "-"
    agent = prompt(win, f"Agente [{current}]: ")
    if not agent:
        state.notify("Asignación cancelada.", "info")
        return False
    return _guard(
        state,
        lambda: actions.assign(ticket["code"], agent, ACTOR),
        f"{ticket['code']} asignado a {agent}.",
    )


def _change_status(win, state: TuiState, ticket: dict[str, Any]) -> bool:
    options = actions.available_statuses(ticket)
    if not options:
        state.notify("No hay transiciones disponibles.", "info")
        return False
    picked = menu(win, f"Estado de {ticket['code']}", options)
    if picked is None:
        return False
    status = options[picked]
    note = None
    if actions.needs_note(ticket, status):
        note = prompt(win, "Motivo de la reapertura: ")
        if not note:
            state.notify("Reabrir requiere un motivo.", "error")
            return False
    if status in ("cancelado", "en_progreso", "pendiente"):
        lines = [f"{ticket['code']}: {ticket['status']} → {status}", ""]
        if note:
            lines.append(f"  Motivo: {note}")
        if not confirm_dialog(win, "Cambiar estado", lines):
            return False
    return _guard(
        state,
        lambda: actions.change_status(ticket["code"], status, note, ACTOR),
        f"{ticket['code']} → {status}.",
    )


def _comment(win, state: TuiState, ticket: dict[str, Any]) -> bool:
    body = prompt(win, "Comentario: ")
    if not body:
        state.notify("Comentario vacío.", "info")
        return False
    return _guard(
        state,
        lambda: actions.comment(ticket["code"], body, ACTOR),
        f"Comentario añadido a {ticket['code']}.",
    )


def _copy(state: TuiState, ticket: dict[str, Any]) -> bool:
    method = actions.copy_text(ticket["code"])
    if method is None:
        state.notify("No se pudo copiar el código.", "error")
        return False
    state.notify(f"{ticket['code']} copiado ({method}).", "ok")
    return False


def _link(win, state: TuiState, ticket: dict[str, Any]) -> bool:
    ref = prompt(win, "Commit: ")
    if not ref:
        state.notify("Enlace cancelado.", "info")
        return False
    return _guard(
        state,
        lambda: actions.link_commit(ticket["code"], ref, ACTOR),
        f"Commit enlazado a {ticket['code']}.",
    )


def run_action(
    win,
    state: TuiState,
    ticket: dict[str, Any] | None,
    refresh: Callable[[bool], None],
) -> None:
    """Menú de acciones del ticket actual."""
    if ticket is None:
        state.notify("No hay ticket seleccionado.", "info")
        return
    picked = menu(win, f"Acciones · {ticket['code']}", list(ACTIONS))
    if picked is None:
        return

    if picked == 0:
        changed = _guard(
            state,
            lambda: actions.claim(ticket["code"], ACTOR),
            f"{ticket['code']} reclamado por {ACTOR}.",
        )
    elif picked == 1:
        changed = _assign(win, state, ticket)
    elif picked == 2:
        changed = _change_status(win, state, ticket)
    elif picked == 3:
        changed = _comment(win, state, ticket)
    elif picked == 4:
        changed = _copy(state, ticket)
    elif picked == 5:
        changed = _link(win, state, ticket)
    else:
        open_session(state, ticket)
        changed = False
    if changed:
        refresh(True)
