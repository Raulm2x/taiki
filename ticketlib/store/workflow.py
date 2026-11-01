"""Máquina de estados y cierre de tickets."""

from __future__ import annotations

import sqlite3
from typing import Any

from .. import db
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
from ..util import normalize_agent, as_list, check_enum, now, normalize_code
from .common import UNSET, event, row_by_code
from .relations import link_ticket
from .tickets import get_ticket


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
) -> dict[str, Any]:
    row = row_by_code(conn, code)
    current = row["status"]
    actor = normalize_agent(actor)

    check_enum(status, STATUSES, "estado")
    check_enum(actor_type, ACTOR_TYPES, "actor_type")

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
) -> dict[str, Any]:
    agent = normalize_agent(agent)
    with db.tx(conn):
        row = row_by_code(conn, code)
        if row["status"] in ("completado", "confirmado", "cancelado"):
            raise TransitionError(
                f"No se puede reclamar {row['code']} porque está '{row['status']}'. "
                "Reábrelo primero."
            )
        if row["assigned_to"] and row["assigned_to"] != agent and not force:
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
        if row["status"] == "en_progreso":
            conn.execute(
                "UPDATE tickets SET assigned_to = ?, updated_at = ? WHERE id = ?",
                (agent, ts, row["id"]),
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

    return ticket


def complete_ticket(
    conn: sqlite3.Connection,
    code: Any,
    resolution: str,
    commit: str | None = None,
    files: Any = None,
    actor: str = "agente",
    actor_type: str = "agente",
    session_id: str | None = None,
) -> dict[str, Any]:
    resolution = (resolution or "").strip()
    if not resolution:
        raise ValidationError("La resolución es obligatoria al completar un ticket")

    transition(
        conn,
        code,
        "completado",
        note=resolution,
        actor=actor,
        actor_type=actor_type,
        session_id=session_id,
        resolution=resolution,
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
