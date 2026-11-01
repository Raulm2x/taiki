"""Protocolo de progreso, leases y reintentos para subagentes.

El conductor no lee el transcript del worker: se apoya en el ticket. Un worker
publica checkpoints (comentario + heartbeat), el conductor detecta leases
caducados y reasigna, y los fallos se reintentan en la misma sesión hasta un
límite; agotado el límite, el ticket queda bloqueado para decisión humana.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

from . import agents, commits, env, repo
from .domain import TicketError, ValidationError

DEFAULT_MAX_ATTEMPTS = 3


def max_attempts() -> int:
    """Límite de intentos: ``TAIKI_MAX_ATTEMPTS`` o 3 por defecto."""
    try:
        return max(1, int(str(env.get("MAX_ATTEMPTS", ""))))
    except (TypeError, ValueError):
        return DEFAULT_MAX_ATTEMPTS


def checkpoint(
    conn: sqlite3.Connection,
    code: Any,
    note: str,
    *,
    pending: str | None = None,
    agent: str = "agente",
    session_id: str | None = None,
) -> dict[str, Any]:
    """Publica qué se hizo, qué falta y renueva el lease del ticket."""
    note = (note or "").strip()
    if not note:
        raise ValidationError("El checkpoint necesita una nota de progreso")
    ticket = repo.get_ticket(conn, code)
    if ticket["status"] != "en_progreso":
        raise TicketError(
            f"{ticket['code']} no está en progreso "
            f"(estado '{ticket['status']}'); un checkpoint solo avanza trabajo"
        )
    text = note if not pending else f"{note} · pendiente: {pending.strip()}"
    repo.comment_ticket(
        conn,
        ticket["code"],
        f"checkpoint: {text}",
        author=agent,
        session_id=session_id,
    )
    renewed = repo.heartbeat_ticket(
        conn, ticket["code"], session_id=session_id, min_interval=0
    )
    current = repo.get_ticket(conn, ticket["code"])
    return {
        "code": current["code"],
        "status": current["status"],
        "checkpoint": text,
        "lease_until": current.get("lease_until"),
        "renewed": renewed,
    }


def reassign_stale(
    conn: sqlite3.Connection,
    *,
    agent: str = "agente",
    session_id: str | None = None,
) -> dict[str, Any]:
    """Reasigna los tickets cuyo lease venció (worker caído o colgado)."""
    stale = repo.stale_claims(conn)
    reassigned: list[str] = []
    for item in stale:
        code = item["code"]
        repo.claim_ticket(
            conn,
            code,
            agent=agent,
            session_id=session_id,
            force=True,
        )
        repo.comment_ticket(
            conn,
            code,
            f"lease caducado ({item['lease_until']}); reasignado a {agent}",
            author=agent,
            session_id=session_id,
        )
        reassigned.append(code)
    return {"reassigned": reassigned, "count": len(reassigned)}


def retry(
    conn: sqlite3.Connection,
    code: Any,
    why: str,
    *,
    agent: str = "agente",
    session_id: str | None = None,
    limit: int | None = None,
) -> dict[str, Any]:
    """Reintenta un fallo en la misma sesión o bloquea al agotar intentos.

    ``iterations`` cuenta los intentos ya abiertos. Un ticket ``en_progreso``
    (worker colgado) se reasigna sin consumir un intento nuevo.
    """
    why = (why or "").strip()
    if not why:
        raise ValidationError("Reintentar requiere el motivo del fallo")
    ticket = repo.get_ticket(conn, code)
    attempts = int(ticket["iterations"] or 0)
    maximum = limit if limit is not None else max_attempts()

    if ticket["status"] == "bloqueado":
        raise TicketError(
            f"{ticket['code']} está bloqueado: requiere decisión humana "
            "antes de reintentar"
        )

    if ticket["status"] == "en_progreso":
        repo.claim_ticket(
            conn,
            ticket["code"],
            agent=agent,
            session_id=session_id,
            force=True,
        )
        repo.comment_ticket(
            conn,
            ticket["code"],
            f"reintento sin reapertura: {why}",
            author=agent,
            session_id=session_id,
        )
        return {
            "code": ticket["code"],
            "status": "en_progreso",
            "attempts": attempts,
            "blocked": False,
            "reassigned": True,
        }

    if attempts >= maximum:
        blocked = repo.transition(
            conn,
            ticket["code"],
            "bloqueado",
            note=(
                f"Reintentos agotados ({attempts}/{maximum}): {why}. "
                "Requiere decisión humana."
            ),
            actor=agent,
            session_id=session_id,
            force=True,
        )
        return {
            "code": blocked["code"],
            "status": blocked["status"],
            "attempts": attempts,
            "blocked": True,
            "reassigned": False,
        }

    reopened = repo.transition(
        conn,
        ticket["code"],
        "en_progreso",
        note=f"Reintento {attempts + 1}/{maximum}: {why}",
        actor=agent,
        session_id=session_id,
        force=True,
    )
    # ``transition`` libera el claim; se devuelve la misma sesión al worker
    # para que continúe sin duplicar el ticket.
    repo.claim_ticket(
        conn,
        reopened["code"],
        agent=agent,
        session_id=session_id,
        force=True,
    )
    current = repo.get_ticket(conn, reopened["code"])
    return {
        "code": current["code"],
        "status": current["status"],
        "attempts": int(current["iterations"] or 0),
        "blocked": False,
        "reassigned": False,
        "session": current.get("owner_session"),
    }


# Rutas que nunca se paralelizan: esquema, store core, configuración compartida
# y agentes. Un cambio aquí puede afectar a cualquier otro writer.
SERIAL_PATTERNS = (
    ".taiki/",
    "opencode.json",
    "ticketlib/db.py",
    "ticketlib/store/",
    "ticketlib/domain.py",
    "tests/budget.toml",
    ".opencode/agents/",
)


def _paths(raw: Any) -> list[str]:
    """Normaliza la columna ``paths`` (JSON o lista) de un ticket."""
    if not raw:
        return []
    if isinstance(raw, list):
        return [str(item) for item in raw]
    try:
        parsed = json.loads(raw)
    except (TypeError, json.JSONDecodeError):
        return [str(raw)]
    return [str(item) for item in parsed] if isinstance(parsed, list) else [str(parsed)]


def _serial_paths(paths: list[str]) -> str | None:
    """Motivo por el que una tarea no puede correr en paralelo, o None."""
    if not paths:
        return "sin paths declarados: no se puede probar que sea disjunta"
    for path in paths:
        if commits.matches(path, SERIAL_PATTERNS):
            return f"toca {path}, reservado a ejecución serial"
    return None


def _writer_profile(
    assigned: Any, kind: Any, config: dict[str, Any] | None = None
) -> str:
    """Perfil que consume presupuesto para un writer ya activo.

    Un perfil específico (``worker-low``, ``worker-high``…) manda; el ``worker``
    genérico o un actor humano no identifican perfil, así que se usa el que el
    router elegiría para el tipo del ticket.
    """
    name = str(assigned or "").strip().lower()
    if name in agents.registry(config) and name != agents.FALLBACK_AGENT:
        return name
    try:
        return str(agents.assign(str(kind or ""), config)["agent"])
    except ValidationError:
        return name or agents.FALLBACK_AGENT


def parallel_plan(
    conn: sqlite3.Connection,
    *,
    project: str | None = None,
    limit: int | None = None,
    config: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Scheduler: qué tareas listas pueden correr a la vez sin pisarse.

    Solo cuentan como writers activos las hojas ``en_progreso``: una épica en
    curso coordina, no escribe, y sus paths amplios no deben serializar a sus
    propias tareas (una épica sin hijos sí cuenta). Los activos consumen el
    presupuesto global y el ``max_parallel`` de su perfil, derivado del asignado
    o, si es genérico, del tipo. El resto va a ``serial`` (una a una) o
    ``deferred`` (presupuesto agotado).
    """
    maximum = limit if limit is not None else agents.concurrency_budget(config)
    if isinstance(maximum, bool) or not isinstance(maximum, int) or maximum < 1:
        raise ValidationError("El límite de paralelismo debe ser un entero >= 1")
    active_sql = (
        "SELECT t.code, t.assigned_to, t.kind, t.paths FROM tickets t "
        "WHERE t.archived = 0 AND t.status = 'en_progreso' "
        "AND NOT EXISTS (SELECT 1 FROM tickets ch "
        "WHERE ch.parent_id = t.id AND ch.archived = 0)"
    )
    active_params: tuple[Any, ...] = ()
    if project:
        active_sql += " AND t.project = ?"
        active_params = (project,)
    active_rows = conn.execute(active_sql, active_params).fetchall()
    active = [
        {
            "code": row["code"],
            "agent": _writer_profile(row["assigned_to"], row["kind"], config),
            "paths": _paths(row["paths"]),
        }
        for row in active_rows
    ]
    active_by_agent: dict[str, int] = {}
    for item in active:
        active_by_agent[item["agent"]] = active_by_agent.get(item["agent"], 0) + 1

    candidates = repo.list_tickets(
        conn,
        statuses=["pendiente", "nuevo"],
        project=project,
        leaves_only=True,
        ready=True,
        order="priority",
        limit=100,
    )
    parallel: list[dict[str, Any]] = []
    serial: list[dict[str, Any]] = []
    deferred: list[str] = []
    parallel_by_agent: dict[str, int] = {}
    for task in candidates:
        paths = _paths(task.get("paths"))
        reason = _serial_paths(paths)
        if reason is not None:
            serial.append({"code": task["code"], "reason": reason})
            continue
        active_clash: list[str] = []
        for item in active:
            active_clash.extend(commits.overlaps(paths, item["paths"]))
        if active_clash:
            serial.append(
                {
                    "code": task["code"],
                    "reason": "solapa con tarea activa: "
                    + ", ".join(sorted(active_clash)),
                }
            )
            continue
        clash: list[str] = []
        for other in parallel:
            clash.extend(commits.overlaps(paths, other["paths"]))
        if clash:
            serial.append(
                {
                    "code": task["code"],
                    "reason": "solapa con " + ", ".join(sorted(clash)),
                }
            )
            continue
        plan = agents.assign(task["kind"], config)
        profile = plan["agent"]
        agent_limit = int(plan["max_parallel"])
        if (
            len(active) + len(parallel) >= maximum
            or parallel_by_agent.get(profile, 0)
            + active_by_agent.get(profile, 0)
            >= agent_limit
        ):
            deferred.append(task["code"])
            continue
        parallel.append({"code": task["code"], "paths": paths})
        parallel_by_agent[profile] = parallel_by_agent.get(profile, 0) + 1
    return {
        "parallel": [item["code"] for item in parallel],
        "serial": serial,
        "deferred": deferred,
        "active": [item["code"] for item in active],
        "limit": maximum,
        "requires_worktree": True,
    }


def isolation_problems(
    code: str,
    *,
    worktree: str | None,
    directory: str | None,
    branch: str | None,
    commit: str | None,
) -> list[str]:
    """Comprueba que el worker está aislado y commiteó en su rama.

    Devuelve una lista de problemas legibles; vacía si el aislamiento es
    correcto. No ejecuta git: el conductor le pasa la rama actual y el commit.
    """
    problems: list[str] = []
    if not worktree:
        problems.append("el ticket no tiene worktree")
    if not directory:
        problems.append("la sesión no publica directorio")
    elif worktree:
        try:
            same = Path(directory).resolve() == Path(worktree).resolve()
        except OSError:
            same = False
        if not same:
            problems.append("directory de la sesión != worktree del ticket")
    if not branch:
        problems.append("la sesión no publica rama")
    elif branch == "main" or not branch.startswith(f"agent/{code}-"):
        problems.append(f"la rama {branch!r} no es agent/{code}-*")
    if not commit:
        problems.append("falta el commit en el checkpoint")
    return problems
