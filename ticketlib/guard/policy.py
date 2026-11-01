"""Política del guard: decidir, crear/reutilizar y explicar."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from .. import db, env, repo, scratch, workspace
from ..domain import TicketError
from ..util import lease_expired, normalize_agent, resolve_scope
from .commands import command_touches_home, path_guarded, risky_git
from .context import auto_title, find_active, find_pending, find_session_epic, session_chain
from .hook import is_mutating

DISABLED_VALUES = {"0", "off", "false", "no", "disabled"}
AUTO_ENV = "GUARD_AUTO"
AUTO_MODES = {"off", "scratch", "ticket", "auto"}
DEFAULT_AUTO = "scratch"


def disabled(environ: dict[str, str] | None = None) -> bool:
    """``TAIKI_GUARD=off`` (o el alias ``TICKETS_GUARD``) desactiva el guard."""
    env_vars = os.environ if environ is None else environ
    return str(env.get("GUARD", "", env_vars)).strip().lower() in DISABLED_VALUES


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


def auto_mode(
    environ: dict[str, str] | None = None,
    config: dict[str, Any] | None = None,
) -> str:
    """Política de creación automática (``scratch`` por defecto).

    Orden de precedencia: ``TAIKI_GUARD_AUTO`` → ``guard_auto`` del
    ``config.json`` del proyecto → ``scratch``.

    - ``scratch``: siempre temporal y oculto (``S-####``). Por defecto: el
      guard nunca fabrica tickets visibles que puedan acabar cancelados.
    - ``auto``: scratch si no hay épica abierta; tarea visible si la hay.
    - ``ticket``: siempre visible, colgado de la épica si existe.
    - ``off``: no crea nada; solo bloquea con instrucciones.
    """
    env_vars = os.environ if environ is None else environ
    value = str(env.get(AUTO_ENV, "", env_vars) or "").strip().lower()
    if value in AUTO_MODES:
        return value
    configured = str((config or {}).get("guard_auto", "") or "").strip().lower()
    if configured in AUTO_MODES:
        return configured
    return DEFAULT_AUTO


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
    short = (title or "")[:60]
    if action == "created":
        if temporary:
            return (
                f"Sin ticket: activé {code} (scratch oculto). Si es real, "
                f"usa promote({code}) o crea el ticket con create; si no, "
                "ignóralo."
            )
        head = f"Sin ticket: activé {code} «{short}»."
        if parent is not None:
            head = (
                f"Sin ticket: activé {code} «{short}» bajo "
                f"{parent['code']}."
            )
        return f"{head} Revisa el título y usa complete {code} al terminar."
    if action == "claimed":
        return (
            f"Sin ticket: activé {code} «{short}», pendiente de esta sesión. "
            f"Usa complete {code} al terminar."
        )
    return (
        "Sin ticket activo y no pude crear uno. Usa create, luego claim "
        "y repite la acción."
    )


def _parallel_message(action: str, others: list[dict[str, Any]]) -> str:
    codes = ", ".join(item["code"] for item in others[:5])
    extra = "" if len(others) <= 5 else f" y {len(others) - 5} más"
    return (
        f"Bloqueado: `{action}` afecta a todo el árbol y hay otras sesiones "
        f"activas ({codes}{extra}). Usa un worktree o stagea rutas concretas."
    )


def other_active_sessions(
    conn: Any, chain: list[str], agent: str
) -> list[dict[str, Any]]:
    """Tickets en progreso que no pertenecen a esta sesión."""
    rows = conn.execute(
        "SELECT t.code, t.assigned_to, t.updated_at, t.lease_until, "
        "COALESCE(t.owner_session, ("
        "  SELECT e.session_id FROM events e "
        "  WHERE e.ticket_id = t.id AND e.session_id IS NOT NULL "
        "  ORDER BY e.id DESC LIMIT 1)) AS sid "
        "FROM tickets t WHERE t.archived = 0 AND t.status = 'en_progreso' "
        "ORDER BY t.updated_at DESC"
    ).fetchall()
    others: list[dict[str, Any]] = []
    for row in rows:
        # Un lease vencido es una sesión muerta: no debe bloquear a las vivas.
        if lease_expired(row["lease_until"]):
            continue
        sid = row["sid"]
        if sid is not None:
            if sid not in chain:
                others.append(dict(row))
        elif row["assigned_to"]:
            # Sin sesión no se puede distinguir entre dos sesiones del mismo
            # agente; se trata como ajena para no compartir el árbol a ciegas.
            others.append(dict(row))
    return others


def shared_tree(environ: dict[str, str] | None = None) -> bool:
    """¿Se permite escribir en el árbol compartido con otras sesiones?"""
    value = str(env.get("SHARED_TREE", "", environ)).strip().lower()
    return bool(value) and value not in DISABLED_VALUES


def _has_worktree(conn: Any, code: str) -> bool:
    return (
        conn.execute(
            "SELECT 1 FROM links l JOIN tickets t ON t.id = l.ticket_id "
            "WHERE t.code = ? AND l.kind = 'worktree' LIMIT 1",
            (code,),
        ).fetchone()
        is not None
    )


def _worktree_message(
    active: dict[str, Any], others: list[dict[str, Any]]
) -> str:
    codes = ", ".join(item["code"] for item in others[:5])
    extra = "" if len(others) <= 5 else f" y {len(others) - 5} más"
    return (
        f"Hay otra sesión activa ({codes}{extra}) y {active['code']} no tiene "
        f"worktree. Usa worktree_create {active['code']} o TAIKI_SHARED_TREE=1."
    )


def _inside_repo(session_base: Path, path: str | None) -> bool:
    """La escritura cae dentro del checkout del proyecto."""
    base = Path(session_base).expanduser().resolve()
    root = workspace.project_root(base).resolve()
    target = Path(path).expanduser().resolve() if path else base
    try:
        target.relative_to(root)
    except ValueError:
        return False
    return True


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
    mutating = is_mutating(tool, path, command)
    risky = risky_git(command) if command else None
    if not mutating and not risky:
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
        enabled = bool(env.get("DB"))
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
        others = other_active_sessions(conn, chain, agent) if mutating or risky else []
        if active is not None:
            # El ticket propio nunca es "otra sesión", aunque no tenga sesión
            # explícita y su fila aparezca en la consulta de activos.
            others = [item for item in others if item["code"] != active["code"]]
        if risky:
            if others:
                return _blocked(
                    "parallel", session, _parallel_message(risky, others)
                )
            if not mutating:
                return _allowed("skipped", session)
        if active is not None:
            if (
                mutating
                and others
                and not shared_tree(environ)
                and _inside_repo(session_base, path)
                and not _has_worktree(conn, active["code"])
            ):
                return _blocked(
                    "worktree",
                    session,
                    _worktree_message(active, others),
                    active["code"],
                )
            repo.heartbeat_ticket(
                conn, active["code"], chain[0] if chain else None
            )
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

        mode = auto_mode(environ, config)
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
