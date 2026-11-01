"""Máquina de estados y cierre de tickets."""

from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta
from typing import Any

from .. import db, env
from ..domain import (
    ACTOR_TYPES,
    PARENT_CONFIRMED_STATUSES,
    PARENT_DONE_STATUSES,
    REOPEN_FROM,
    STATUSES,
    TRANSITIONS,
    NotFoundError,
    PermissionDenied,
    TransitionError,
    ValidationError,
)
from ..gitutil import resolve_commit
from ..util import (
    as_list,
    check_enum,
    lease_expired,
    normalize_agent,
    normalize_code,
    now,
)
from .common import UNSET, event, row_by_code
from .relations import link_ticket
from .queries import get_ticket

DEFAULT_LEASE_MINUTES = 30


def _lease_minutes() -> int:
    try:
        return max(1, int(str(env.get("LEASE_MINUTES", ""))))
    except (TypeError, ValueError):
        return DEFAULT_LEASE_MINUTES


def _future(minutes: int) -> str:
    moment = datetime.now().astimezone() + timedelta(minutes=minutes)
    return moment.isoformat(timespec="seconds")


def _expired(value: str | None, at: str | None = None) -> bool:
    """Alias histórico de ``util.lease_expired``."""
    return lease_expired(value, at)


def transition(
    conn: sqlite3.Connection,
    code: Any,
    status: str,
    note: str | None = None,
    actor: str = "agente",
    actor_type: str = "agente",
    session_id: str | None = None,
    assigned_to: Any = UNSET,
    resolution: Any = UNSET,
    force: bool = False,
) -> dict[str, Any]:
    row = row_by_code(conn, code)
    current = row["status"]
    actor = normalize_agent(actor)

    check_enum(status, STATUSES, "estado")
    check_enum(actor_type, ACTOR_TYPES, "actor_type")

    owner = row["owner_session"]
    if current == "en_progreso" and owner and not force and actor_type != "usuario":
        same = session_id == owner or (
            session_id is None and owner == f"agent:{actor}"
        )
        if not same:
            raise PermissionDenied(
                f"{row['code']} está reclamado por otra sesión ({owner}); "
                "usa force=true si de verdad debes cambiarlo."
            )

    if status == "confirmado" and actor_type != "usuario":
        raise PermissionDenied(
            "Solo el usuario puede confirmar un ticket. "
            "Deja el ticket en 'completado' y pide confirmación con el CLI."
        )

    if status == current:
        raise TransitionError(f"El ticket {row['code']} ya está en estado '{status}'")

    children = conn.execute(
        "SELECT code, status FROM tickets WHERE parent_id = ? "
        "AND archived = 0 ORDER BY id",
        (row["id"],),
    ).fetchall()

    allowed = set(TRANSITIONS.get(current, set()))
    if status == "completado" and children:
        # El cierre de una épica se valida contra sus tareas más abajo; así el
        # error explica qué tareas faltan en vez de hablar de la transición.
        allowed.add("completado")
    if status not in allowed:
        raise TransitionError(
            f"Transición no permitida: {current} → {status}. "
            f"Desde '{current}' se permite: {', '.join(sorted(allowed)) or 'ninguna'}"
        )

    note = (note or "").strip() or None
    if status in ("en_progreso", "pendiente") and current in REOPEN_FROM and not note:
        raise ValidationError("Reabrir un ticket requiere un motivo en 'note'")

    if status in ("completado", "confirmado"):
        closed = (
            PARENT_DONE_STATUSES
            if status == "completado"
            else PARENT_CONFIRMED_STATUSES
        )
        pending_children = [
            child for child in children if child["status"] not in closed
        ]
        if pending_children:
            detail = ", ".join(
                f"{child['code']} ({child['status']})"
                for child in pending_children[:5]
            )
            raise TransitionError(
                f"{row['code']} tiene {len(pending_children)} tarea(s) sin "
                f"cerrar: {detail}"
            )

    ts = now()
    changes: list[tuple[str, Any]] = [("status", status), ("updated_at", ts)]

    if status == "en_progreso" and not row["started_at"]:
        changes.append(("started_at", ts))
    if status == "completado":
        changes.append(("completed_at", ts))
        changes.append(("iterations", int(row["iterations"] or 0) + 1))
    elif current in ("completado", "confirmado"):
        changes.append(("completed_at", None))
    if status == "confirmado":
        changes.append(("confirmed_at", ts))
    elif current == "confirmado":
        changes.append(("confirmed_at", None))
    if assigned_to is not UNSET:
        changes.append(("assigned_to", assigned_to))
    if resolution is not UNSET:
        changes.append(("resolution", resolution))
    # Al cambiar de estado el claim deja de ser válido: se libera la sesión.
    changes.append(("owner_session", None))
    changes.append(("lease_until", None))
    changes.append(("heartbeat_at", None))

    with db.tx(conn):
        sets = ", ".join(f"{name} = ?" for name, _ in changes)
        conn.execute(
            f"UPDATE tickets SET {sets} WHERE id = ?",
            (*[value for _, value in changes], row["id"]),
        )
        event(
            conn,
            row["id"],
            "transicion",
            actor,
            actor_type,
            session_id,
            current,
            status,
            note,
        )

    return get_ticket(conn, code)


def claim_ticket(
    conn: sqlite3.Connection,
    code: Any,
    agent: str = "agente",
    actor_type: str = "agente",
    session_id: str | None = None,
    force: bool = False,
    steal: bool = False,
    anonymous_owner: bool = False,
    lease_minutes: int | None = None,
) -> dict[str, Any]:
    agent = normalize_agent(agent)
    with db.tx(conn):
        row = row_by_code(conn, code)
        if row["status"] in ("completado", "confirmado", "cancelado"):
            raise TransitionError(
                f"No se puede reclamar {row['code']} porque está '{row['status']}'. "
                "Reábrelo primero."
            )
        if (
            row["assigned_to"]
            and row["assigned_to"] != agent
            and not force
            and not steal
        ):
            raise TransitionError(
                f"{row['code']} ya está asignado a '{row['assigned_to']}'"
            )
        if conn.execute(
            "SELECT 1 FROM tickets WHERE parent_id = ? AND archived = 0 LIMIT 1",
            (row["id"],),
        ).fetchone():
            raise TransitionError(
                f"{row['code']} es una épica; reclama una de sus tareas"
            )

        ts = now()
        owner = row["owner_session"]
        # En el camino MCP, un agente sin sesión usa un dueño estable por
        # nombre. Así dos claims anónimos del mismo agente no se pisan en
        # silencio. El API interno/CLI conserva el comportamiento histórico.
        owner_key = session_id or (
            f"agent:{agent}"
            if anonymous_owner and actor_type == "agente"
            else None
        )
        if (
            row["status"] == "en_progreso"
            and owner
            and (owner_key is None or owner != owner_key)
            and not force
            and not steal
        ):
            raise TransitionError(
                f"{row['code']} está en progreso en la sesión {owner}. "
                "Espera a que termine o usa steal=true si el lease caducó."
            )
        if (
            steal
            and row["status"] == "en_progreso"
            and owner
            and (owner_key is None or owner != owner_key)
            and not force
            and not _expired(row["lease_until"], now())
        ):
            raise TransitionError(
                f"{row['code']} tiene un lease vigente hasta "
                f"{row['lease_until']}; usa force=true para forzarlo."
            )
        if owner_key and not force:
            other = conn.execute(
                "SELECT code FROM tickets WHERE archived = 0 "
                "AND status = 'en_progreso' AND owner_session = ? "
                "AND id != ? LIMIT 1",
                (owner_key, row["id"]),
            ).fetchone()
            if other is not None:
                raise TransitionError(
                    f"La sesión ya tiene {other['code']} en progreso; "
                    "complétalo antes de reclamar otro ticket."
                )
        try:
            minutes = max(1, min(int(lease_minutes or _lease_minutes()), 24 * 60))
        except (TypeError, ValueError):
            minutes = _lease_minutes()
        lease_until = _future(minutes)
        if row["status"] == "en_progreso":
            conn.execute(
                "UPDATE tickets SET assigned_to = ?, updated_at = ?, "
                "owner_session = ?, heartbeat_at = ?, lease_until = ? "
                "WHERE id = ?",
                (agent, ts, owner_key, ts, lease_until, row["id"]),
            )
            event(
                conn,
                row["id"],
                "reclamar",
                agent,
                actor_type,
                session_id,
                "en_progreso",
                "en_progreso",
                f"Asignado a {agent}",
            )
            ticket = get_ticket(conn, code)
        else:
            ticket = transition(
                conn,
                row["code"],
                "en_progreso",
                note=f"Reclamado por {agent}",
                actor=agent,
                actor_type=actor_type,
                session_id=session_id,
                assigned_to=agent,
            )
            conn.execute(
                "UPDATE tickets SET owner_session = ?, heartbeat_at = ?, "
                "lease_until = ? WHERE id = ?",
                (owner_key, ts, lease_until, row["id"]),
            )
            ticket = get_ticket(conn, code)

    return ticket


def heartbeat_ticket(
    conn: sqlite3.Connection,
    code: Any,
    session_id: str | None = None,
    *,
    minutes: int | None = None,
    min_interval: int = 60,
) -> bool:
    """Renueva el lease del ticket activo (si la sesión es la dueña)."""
    row = row_by_code(conn, code)
    if row["status"] != "en_progreso":
        return False
    owner = row["owner_session"]
    if owner and session_id and owner != session_id:
        return False
    last = row["heartbeat_at"]
    if last:
        try:
            elapsed = (
                datetime.fromisoformat(now()) - datetime.fromisoformat(last)
            ).total_seconds()
            if elapsed < min_interval:
                return False
        except ValueError:
            pass
    ts = now()
    with db.tx(conn):
        conn.execute(
            "UPDATE tickets SET heartbeat_at = ?, lease_until = ?, "
            "updated_at = ? WHERE id = ?",
            (ts, _future(minutes or _lease_minutes()), ts, row["id"]),
        )
    return True


def stale_claims(
    conn: sqlite3.Connection, at: str | None = None
) -> list[dict[str, Any]]:
    """Tickets en progreso cuyo lease venció (agente muerto o colgado)."""
    rows = conn.execute(
        "SELECT * FROM tickets WHERE archived = 0 AND status = 'en_progreso' "
        "AND owner_session IS NOT NULL AND lease_until IS NOT NULL "
        "ORDER BY lease_until"
    ).fetchall()
    return [dict(row) for row in rows if _expired(row["lease_until"], at)]


def complete_ticket(
    conn: sqlite3.Connection,
    code: Any,
    resolution: str,
    commit: str | None = None,
    files: Any = None,
    actor: str = "agente",
    actor_type: str = "agente",
    session_id: str | None = None,
    force: bool = False,
) -> dict[str, Any]:
    resolution = (resolution or "").strip()
    if not resolution:
        raise ValidationError("La resolución es obligatoria al completar un ticket")

    # Resolver git antes de abrir la transacción: rev-parse puede tardar y no
    # debe mantener el lock de escritura mientras otro proceso espera.
    if commit:
        commit = resolve_commit(commit) or commit

    # Una sola transacción: o se completa y se enlazan commit/archivos, o no
    # queda nada. ``db.tx`` es reentrante, así que transition/link_ticket se
    # suman a esta transacción en vez de abrir la suya.
    with db.tx(conn):
        transition(
            conn,
            code,
            "completado",
            note=resolution,
            actor=actor,
            actor_type=actor_type,
            session_id=session_id,
            resolution=resolution,
            force=force,
        )
        if commit:
            link_ticket(
                conn,
                code,
                "commit",
                commit,
                actor=actor,
                actor_type=actor_type,
                session_id=session_id,
                resolve=False,
            )
        for file_ref in as_list(files):
            link_ticket(
                conn,
                code,
                "archivo",
                file_ref,
                actor=actor,
                actor_type=actor_type,
                session_id=session_id,
            )
    return get_ticket(conn, code)


def confirm_tickets(
    conn: sqlite3.Connection,
    codes: Any,
    note: str | None = None,
    actor: str = "usuario",
    actor_type: str = "usuario",
) -> dict[str, Any]:
    """Confirma varios tickets de forma atómica.

    Se valida todo antes de escribir: si algún código no existe o no está en
    ``completado`` no se confirma ninguno. Los que ya estaban confirmados se
    omiten (idempotente) y se informan aparte.
    """
    if isinstance(codes, str):
        codes = [codes]
    codes = list(codes or [])
    if not codes:
        raise ValidationError("Indica al menos un ticket para confirmar")

    ordered: list[Any] = []
    errors: dict[str, str] = {}
    for raw in codes:
        try:
            key = normalize_code(raw)
        except ValidationError as exc:
            errors[str(raw)] = str(exc)
            continue
        if key not in ordered:
            ordered.append(key)

    already: list[str] = []
    to_confirm: list[str] = []
    seen: set[str] = set()

    def schedule(code: str) -> None:
        if code not in seen:
            seen.add(code)
            to_confirm.append(code)

    for key in ordered:
        try:
            row = row_by_code(conn, key)
        except NotFoundError:
            errors[str(key)] = "no existe"
            continue
        if row["status"] == "confirmado":
            already.append(row["code"])
            continue
        scratch_tag = conn.execute(
            "SELECT 1 FROM tags WHERE ticket_id = ? AND tag = '_scratch'",
            (row["id"],),
        ).fetchone()
        if scratch_tag:
            errors[row["code"]] = (
                "es un ticket scratch; usa `taiki scratch --promote` "
                "o `--drop`"
            )
            continue
        if row["status"] != "completado":
            errors[row["code"]] = (
                f"estado '{row['status']}' (debe estar en 'completado')"
            )
            continue

        children = conn.execute(
            "SELECT code, status FROM tickets WHERE parent_id = ? "
            "AND archived = 0 ORDER BY id",
            (row["id"],),
        ).fetchall()
        if children:
            pending = [
                child
                for child in children
                if child["status"] not in PARENT_DONE_STATUSES
            ]
            if pending:
                errors[row["code"]] = (
                    "tareas abiertas: "
                    + ", ".join(
                        f"{child['code']} ({child['status']})"
                        for child in pending[:5]
                    )
                )
                continue
            # Primero las tareas completadas, y después la épica.
            for child in children:
                if child["status"] == "completado":
                    schedule(child["code"])
        schedule(row["code"])

    report: dict[str, Any] = {
        "confirmed": [],
        "already": already,
        "errors": errors,
        "requested": len(codes),
    }
    if errors:
        return report

    note = (note or "").strip() or "Confirmado por el usuario"
    with db.tx(conn):
        for code in to_confirm:
            transition(
                conn,
                code,
                "confirmado",
                note=note,
                actor=actor,
                actor_type=actor_type,
            )
            report["confirmed"].append(code)
    return report
