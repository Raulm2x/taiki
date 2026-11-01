"""Política del guard: decidir, crear/reutilizar y explicar."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from .. import db, repo, scratch, workspace
from ..domain import TicketError
from ..util import normalize_agent, resolve_scope
from .commands import command_touches_home, path_guarded
from .context import auto_title, find_active, find_pending, find_session_epic, session_chain
from .hook import is_mutating

DISABLED_VALUES = {"0", "off", "false", "no", "disabled"}
AUTO_ENV = "TICKETS_GUARD_AUTO"
AUTO_MODES = {"off", "scratch", "ticket", "auto"}


def disabled(environ: dict[str, str] | None = None) -> bool:
    """``TICKETS_GUARD=off`` desactiva el guard (escape hatch manual)."""
    env = os.environ if environ is None else environ
    return str(env.get("TICKETS_GUARD", "")).strip().lower() in DISABLED_VALUES


def _allowed(
    action: str, session: str | None, ticket: str | None = None
) -> dict[str, Any]:
    return {
        "allow": True,
        "action": action,
        "ticket": ticket,
        "session": session,
        "message": "",
    }


def _blocked(
    action: str, session: str | None, message: str, ticket: str | None = None
) -> dict[str, Any]:
    return {
        "allow": False,
        "action": action,
        "ticket": ticket,
        "session": session,
        "message": message,
    }


def auto_mode(environ: dict[str, str] | None = None) -> str:
    """Política de creación automática (``auto`` por defecto).

    - ``auto``: scratch si no hay épica abierta; tarea visible si la hay.
    - ``scratch``: siempre temporal y oculto.
    - ``ticket``: siempre visible (comportamiento anterior).
    - ``off``: no crea nada; solo bloquea con instrucciones.
    """
    env = os.environ if environ is None else environ
    value = str(env.get(AUTO_ENV, "")).strip().lower()
    return value if value in AUTO_MODES else "auto"


def _create_and_claim(
    conn: Any,
    *,
    chain: list[str],
    agent: str,
    base: Path,
    parent: dict[str, Any] | None = None,
    temporary: bool = False,
) -> dict[str, Any]:
    session_id = chain[0] if chain else None
    project, path = resolve_scope(None, str(base))
    title = auto_title(session_id, agent, base)
    if temporary:
        return scratch.create_or_reuse(
            conn,
            agent=agent,
            session_id=session_id,
            title=title,
            ttl=scratch.DEFAULT_TTL,
        )
    body = (
        "Creado automáticamente por el guard al detectar un cambio sin ticket "
        "activo.\n"
        f"Agente: {agent}\n"
        f"Directorio: {path}\n"
    )
    if parent is not None:
        body += (
            f"Tarea de la épica {parent['code']} «{parent['title']}», "
            "que aún no está confirmada.\n"
        )
    ticket = repo.create_ticket(
        conn,
        title=title,
        body=body,
        kind="otro",
        priority="media",
        project=project,
        path=path,
        tags=["auto"],
        parent_id=parent["id"] if parent is not None else None,
        created_by=agent,
        actor_type="agente",
        session_id=session_id,
        assigned_to=agent,
    )
    repo.claim_ticket(
        conn,
        ticket["code"],
        agent=agent,
        session_id=session_id,
    )
    return ticket


def _message(
    action: str,
    code: str,
    title: str,
    parent: dict[str, Any] | None = None,
    temporary: bool = False,
) -> str:
    if action == "created":
        if temporary:
            return (
                f"Sin ticket activo: creé {code} como ticket temporal (oculto) "
                "y lo activé.\n"
                f"Si el trabajo es real, promuévelo con "
                f"`ticket scratch --promote {code}` o crea el ticket adecuado; "
                "si no, ignóralo (caduca solo y no aparece en la tabla)."
            )
        head = f"Sin ticket activo: creé y activé {code} «{title}»."
        if parent is not None:
            head += (
                f"\nLo colgué como tarea de la épica {parent['code']} "
                f"«{parent['title']}», que todavía no está confirmada."
            )
    elif action == "claimed":
        head = (
            f"Sin ticket activo: activé {code} «{title}», "
            "que ya estaba pendiente en esta sesión."
        )
    else:
        return (
            "Sin ticket activo y no se pudo crear uno automáticamente. "
            "Crea el ticket con tickets_create, reclámalo con tickets_claim "
            "y repite la acción."
        )
    return (
        f"{head}\n"
        "Revisa el título y el cuerpo con tickets_update si hace falta, "
        f"sigue con el trabajo y al terminar usa tickets_complete {code}."
    )


def evaluate(
    *,
    tool: str,
    session: str | None = None,
    agent: str = "opencode",
    path: str | None = None,
    command: str | None = None,
    cwd: str | Path | None = None,
    auto: bool = True,
    environ: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Decide si la acción pasa, creando/reclamando ticket si hace falta."""
    agent = normalize_agent(agent)
    if disabled(environ):
        return _allowed("disabled", session)
    if not is_mutating(tool, path, command):
        return _allowed("skipped", session)
    if path and not path_guarded(path):
        return _allowed("outside", session)
    if not path and command and not command_touches_home(command, cwd):
        return _allowed("outside", session)
    session_base = Path(cwd).expanduser() if cwd else Path.cwd()
    # El guard es opt-in: solo actúa si el proyecto lo activa en .taiki/config.json.
    # Con TAIKI_DB/TICKETS_DB explícito se asume activado (tests y usos manuales).
    config = workspace.load_config(workspace.find_workspace(session_base))
    enabled = config.get("guard")
    if enabled is None:
        enabled = bool(os.environ.get("TAIKI_DB") or os.environ.get("TICKETS_DB"))
    if not enabled:
        return _allowed("disabled", session)
    try:
        target = db.db_path(session_base)
    except TicketError:
        return _allowed("no-db", session)
    if not target.exists():
        return _allowed("no-db", session)

    base = session_base
    if path:
        base = Path(path).expanduser().parent

    conn = db.connect(target)
    try:
        chain = session_chain(session)
        active = find_active(conn, chain, agent)
        if active is not None:
            return _allowed("active", session, active["code"])

        pending = find_pending(conn, chain)
        if pending is not None:
            try:
                claimed = repo.claim_ticket(
                    conn,
                    pending["code"],
                    agent=agent,
                    session_id=chain[0] if chain else None,
                )
            except TicketError:
                # Otro agente se lo quedó entre la consulta y el claim.
                return _blocked("blocked", session, _message("blocked", "", ""))
            return _blocked(
                "claimed",
                session,
                _message("claimed", claimed["code"], claimed["title"]),
                claimed["code"],
            )

        mode = auto_mode(environ)
        if not auto or mode == "off":
            return _blocked("blocked", session, _message("blocked", "", ""))

        project, _path = resolve_scope(None, str(base))
        epic = find_session_epic(conn, chain, project)
        temporary = mode == "scratch" or (mode == "auto" and epic is None)
        created = _create_and_claim(
            conn,
            chain=chain,
            agent=agent,
            base=base,
            parent=None if temporary else epic,
            temporary=temporary,
        )
        return _blocked(
            "created",
            session,
            _message(
                "created",
                created["code"],
                created["title"],
                None if temporary else epic,
                temporary,
            ),
            created["code"],
        )
    finally:
        conn.close()
