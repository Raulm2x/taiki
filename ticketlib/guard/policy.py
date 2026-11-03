"""Política del guard: decidir, crear/reutilizar y explicar."""

from __future__ import annotations

import os
import shlex
from pathlib import Path
from typing import Any

from .. import db, env, repo, scratch, workspace
from ..commits import PROTECTED_BRANCHES, current_branch, overlaps
from ..domain import TicketError
from ..util import lease_expired, normalize_agent, resolve_scope
from .commands import (
    command_runs_code,
    command_scope_problem,
    command_touches_home,
    path_guarded,
    path_in_scope,
    resolve_roots,
    risky_git,
    split_commands,
)
from .context import (
    auto_title,
    find_active,
    find_pending,
    find_session_epic,
    session_chain,
    ticket_isolation,
    ticket_paths,
    ticket_resources,
)
from .hook import is_mutating

DISABLED_VALUES = {"0", "off", "false", "no", "disabled"}
# Subcomandos de git que escriben la historia o publican una rama.
BRANCH_WRITES = {"commit", "merge", "push"}
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


def _git_call(segment: str) -> tuple[str, list[str]] | None:
    """Subcomando y argumentos del primer ``git`` de un segmento."""
    try:
        tokens = shlex.split(segment, posix=True)
    except ValueError:
        tokens = segment.split()
    if "git" not in tokens:
        return None
    rest = tokens[tokens.index("git") + 1 :]
    while rest and rest[0].startswith("-"):
        rest = rest[1:]
    if not rest:
        return None
    return rest[0], rest[1:]


def _push_refs(args: list[str]) -> list[str]:
    """Ramas destino de un ``git push`` (sin remoto, opciones ni ``+``)."""
    refs: list[str] = []
    for arg in args:
        if arg.startswith("-"):
            continue
        ref = arg.lstrip("+").rsplit(":", 1)[-1]
        if ref.startswith("refs/heads/"):
            ref = ref[len("refs/heads/") :]
        refs.append(ref)
    return refs


def protected_git(command: str | None, branch: str | None = None) -> str | None:
    """Etiqueta del comando git que escribe sobre ``main``/``master``.

    Un agente nunca debe commitear, mergear ni publicar directamente en la
    rama protegida: solo ``taiki release`` escribe ``main``. El push también se
    bloquea cuando el refspec apunta a la rama protegida desde otra rama.
    """
    for segment in split_commands(command or ""):
        call = _git_call(segment)
        if call is None:
            continue
        sub, args = call
        if sub not in BRANCH_WRITES:
            continue
        if branch in PROTECTED_BRANCHES:
            return f"git {sub}"
        if sub == "push" and any(
            ref in PROTECTED_BRANCHES for ref in _push_refs(args)
        ):
            return "git push"
    return None


def _protected_message(action: str) -> str:
    return (
        f"Bloqueado: `{action}` sobre main/master. Solo `taiki release` escribe "
        "main; trabaja en una rama agent/T-XXXX y usa `taiki worktree merge`."
    )


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
        "t.paths, t.resources, t.isolation, "
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


def _scope_base(workspace_dir: Path | None, session_base: Path) -> Path:
    """Raíz contra la que se resuelven paths y raíces relativas."""
    if workspace_dir is not None:
        return workspace_dir.parent
    return session_base


def _guard_roots(
    workspace_dir: Path | None, scope_base: Path, config: dict[str, Any]
) -> list[Path]:
    """HOME + raíz del proyecto + ``scope.roots``, resueltas contra la raíz.

    La raíz del proyecto se incluye siempre: una config con ``["/etc"]`` no
    debe dejar el repositorio fuera del guard.
    """
    configured: list[str] = []
    try:
        configured = workspace.scope_roots(
            config, workspace.load_local(workspace_dir)
        )
    except TicketError:
        configured = ["."]
    values: list[str | Path] = [Path.home(), scope_base, *configured]
    return list(resolve_roots(values, scope_base))


def _serial_message(action: str, others: list[dict[str, Any]]) -> str:
    codes = ", ".join(item["code"] for item in others[:5])
    extra = "" if len(others) <= 5 else f" y {len(others) - 5} más"
    return (
        f"Bloqueado: `{action}` con aislamiento none y otra sesión activa "
        f"({codes}{extra}). Espera o usa worktree."
    )


def _scope_message(detail: str, code: str) -> str:
    return (
        f"Bloqueado: {detail} no está en el scope de {code}. Un agente no "
        f"amplía el scope: pide `taiki update {code} --paths <ruta>`."
    )


def _overlap_message(code: str, clash: dict[str, Any]) -> str:
    shared = clash["paths"][:3] + clash["resources"][:3]
    detail = ", ".join(shared) if shared else "paths/resources"
    return (
        f"Bloqueado: el scope de {code} solapa con {clash['code']} "
        f"({detail}). Serializa o espera a que termine."
    )


def _scope_clash(
    active: dict[str, Any], others: list[dict[str, Any]]
) -> dict[str, Any] | None:
    """Primer ticket activo de otra sesión con paths o recursos solapados."""
    left_paths = ticket_paths(active)
    left_resources = set(ticket_resources(active))
    for other in others:
        hits = overlaps(left_paths, ticket_paths(other))
        shared = sorted(left_resources & set(ticket_resources(other)))
        if hits or shared:
            return {"code": other["code"], "paths": hits, "resources": shared}
    return None


def _scope_problem(
    active: dict[str, Any],
    *,
    path: str | None,
    command: str | None,
    base: Path,
    cwd: Path | None = None,
) -> str | None:
    """Detalle de la escritura que se sale del scope, o ``None`` si cabe.

    ``base`` resuelve los ``paths`` del ticket (raíz del proyecto); ``cwd``
    resuelve destinos relativos de comandos (directorio real de la sesión).

    Decisión fail-closed: ``isolation=scope`` sin ``paths`` no permite nada.
    ``commits.check`` aún trata el scope vacío como "sin restricción"; unificar
    exige tocar ``store/mutations.py`` (validar en create/update) o
    ``commits.py``, fuera de los paths de este ticket.
    """
    scopes = ticket_paths(active)
    resources = ticket_resources(active)
    if not scopes:
        return "el ticket no declara paths (scope vacío)"
    if path:
        if path_in_scope(path, scopes, base, cwd):
            return None
        return f"la ruta {path!r}"
    if command:
        return command_scope_problem(command, scopes, resources, base, cwd)
    return "la escritura sin ruta"


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
    branch = None
    protected = None
    if command and "git" in command:
        branch = current_branch(cwd or ".")
        protected = protected_git(command, branch)
    if (
        not mutating
        and not risky
        and not protected
        and not (command and command_runs_code(command))
    ):
        return _allowed("skipped", session)
    session_base = Path(cwd).expanduser() if cwd else Path.cwd()
    # El guard es opt-in: solo actúa si el proyecto lo activa en .taiki/config.json.
    # Con TAIKI_DB/TICKETS_DB explícito se asume activado (tests y usos manuales).
    workspace_dir = workspace.find_workspace(session_base)
    config = workspace.load_config(workspace_dir)
    enabled = config.get("guard")
    if enabled is None:
        enabled = bool(env.get("DB"))
    if not enabled:
        return _allowed("disabled", session)
    scope_base = _scope_base(workspace_dir, session_base)
    roots = _guard_roots(workspace_dir, scope_base, config)
    if path and not path_guarded(path, roots, scope_base):
        return _allowed("outside", session)
    if not path and command and not command_touches_home(
        command, scope_base, roots
    ):
        return _allowed("outside", session)
    try:
        target = db.db_path(session_base)
    except TicketError:
        return _allowed("no-db", session)
    if not target.exists():
        return _allowed("no-db", session)
    if protected:
        return _blocked("protected-branch", session, _protected_message(protected))

    base = session_base
    if path:
        base = Path(path).expanduser().parent

    conn = db.connect(target)
    try:
        chain = session_chain(session)
        active = find_active(conn, chain, agent)
        # En scope, ejecutar código pegado (``python -c``, ``sh -c``) cuenta
        # como escritura aunque no se pueda leer el cuerpo: el guard no adivina.
        if (
            not mutating
            and command
            and active is not None
            and ticket_isolation(active) == "scope"
            and command_runs_code(command)
        ):
            mutating = True
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
            mode = ticket_isolation(active)
            if mutating:
                action = path or command or "escritura"
                if mode == "none":
                    if others and not shared_tree(environ):
                        return _blocked(
                            "serial",
                            session,
                            _serial_message(action, others),
                            active["code"],
                        )
                elif mode == "scope":
                    clash = _scope_clash(active, others)
                    if clash is not None:
                        return _blocked(
                            "scope-overlap",
                            session,
                            _overlap_message(active["code"], clash),
                            active["code"],
                        )
                    problem = _scope_problem(
                        active,
                        path=path,
                        command=command,
                        base=scope_base,
                        cwd=session_base,
                    )
                    if problem is not None:
                        return _blocked(
                            "scope",
                            session,
                            _scope_message(problem, active["code"]),
                            active["code"],
                        )
                elif (
                    others
                    and not shared_tree(environ)
                    and _inside_repo(session_base, path)
                    and not _has_worktree(conn, active["code"])
                ):
                    # Modo worktree: comportamiento clásico; no valida paths.
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
