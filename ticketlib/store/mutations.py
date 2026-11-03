"""Mutaciones y modificaciones de tickets en el almacén de datos."""

from __future__ import annotations

import json
import re
import sqlite3
from typing import Any

from .. import db
from ..domain import (
    KINDS,
    PRIORITIES,
    SCRATCH_CODE_PREFIX,
    SCRATCH_COUNTER,
    TICKET_COUNTER,
    TOMBSTONE_TAG,
    ValidationError,
    clean_isolation,
    clean_resources,
    clean_reversal,
)
from ..util import check_enum, clean_paths, clean_tags, normalize_agent, now
from .common import UNSET, event, row_by_code
from .queries import get_ticket, missing_codes

__all__ = [
    "allocate_code",
    "attach_tickets",
    "create_ticket",
    "detach_tickets",
    "fill_gaps",
    "renumber_children",
    "renumber_plan",
    "update_ticket",
]


def _next_child_code(conn: sqlite3.Connection, parent_code: str) -> str:
    """Siguiente código de tarea: ``T-0014.1``, ``T-0014.2``…"""
    rows = conn.execute(
        "SELECT code FROM tickets WHERE code LIKE ?",
        (f"{parent_code}.%",),
    ).fetchall()
    highest = 0
    for row in rows:
        suffix = row["code"].rsplit(".", 1)[-1]
        if suffix.isdigit():
            highest = max(highest, int(suffix))
    return f"{parent_code}.{highest + 1:02d}"


def _has_children(conn: sqlite3.Connection, ticket_id: int) -> bool:
    row = conn.execute(
        "SELECT 1 FROM tickets WHERE parent_id = ? AND archived = 0 LIMIT 1",
        (ticket_id,),
    ).fetchone()
    return row is not None


def validate_parent(
    conn: sqlite3.Connection,
    parent_id: Any,
    child_row: sqlite3.Row | None = None,
) -> int | None:
    """Valida y resuelve el padre: un solo nivel, sin ciclos ni archivados."""
    if parent_id is None or not str(parent_id).strip():
        return None
    parent = row_by_code(conn, parent_id)
    if parent["archived"]:
        raise ValidationError(f"El padre {parent['code']} está archivado")
    if parent["parent_id"] is not None:
        raise ValidationError(
            f"{parent['code']} ya es una tarea; solo se admite un nivel "
            "(épica → tarea)"
        )
    if child_row is not None:
        if parent["id"] == child_row["id"]:
            raise ValidationError("Un ticket no puede ser su propio padre")
        if _has_children(conn, child_row["id"]):
            raise ValidationError(
                f"{child_row['code']} tiene tareas; una épica no puede "
                "convertirse en tarea"
            )
    return parent["id"]


def _counter_key(prefix: str) -> str:
    """Clave de ``meta`` del contador de cada espacio de códigos."""
    scratch = SCRATCH_CODE_PREFIX.rstrip("-")
    return SCRATCH_COUNTER if prefix == scratch else TICKET_COUNTER


def allocate_code(conn: sqlite3.Connection, prefix: str = "T") -> str:
    """Siguiente código del espacio indicado (``T-0001``, ``S-0001``…)."""
    key = _counter_key(prefix)
    number = int(db.meta_get(conn, key, "1") or "1")
    db.meta_set(conn, key, str(number + 1))
    return f"{prefix}-{number:04d}"


def create_ticket(
    conn: sqlite3.Connection,
    title: str,
    body: str = "",
    kind: str = "otro",
    priority: str = "media",
    project: str | None = None,
    path: str | None = None,
    tags: Any = None,
    paths: Any = None,
    parent_id: Any = None,
    created_by: str = "agente",
    actor_type: str = "agente",
    session_id: str | None = None,
    assigned_to: str | None = None,
    isolation: Any = None,
    resources: Any = None,
    reversal: Any = None,
    code_prefix: str = "T",
    code: str | None = None,
) -> dict[str, Any]:
    """Crea un nuevo ticket con código secuencial y evento inicial.

    ``code_prefix`` elige el espacio de numeración: ``T`` para tickets
    visibles y ``S`` para temporales (scratch), que no consumen números
    T-XXXX. ``code`` fuerza un código concreto y solo se usa para cubrir
    huecos históricos con tickets genéricos.

    ``assigned_to`` se rellena cuando el agente que crea el ticket se
    identifica, para que la columna AGENTE sea visible antes del ``claim``.
    """
    title = (title or "").strip()
    if not title:
        raise ValidationError("El título del ticket es obligatorio")
    created_by = normalize_agent(created_by)
    if assigned_to:
        assigned_to = normalize_agent(assigned_to)
    check_enum(kind, KINDS, "tipo")
    check_enum(priority, PRIORITIES, "prioridad")
    check_enum(actor_type, ("agente", "usuario"), "actor_type")

    parent = validate_parent(conn, parent_id)
    parent_row = (
        row_by_code(conn, parent_id)
        if parent is not None
        else None
    )

    tags = clean_tags(tags)
    scope = clean_paths(paths)
    isolation = clean_isolation(isolation)
    resources = clean_resources(resources)
    reversal = clean_reversal(reversal)
    ts = now()

    with db.tx(conn):
        if code is not None:
            forced = str(code).strip().upper()
            if not re.fullmatch(r"T-\d{4,}", forced):
                raise ValidationError(
                    f"Código forzado inválido: {code!r} (esperado T-0001)"
                )
            if conn.execute(
                "SELECT 1 FROM tickets WHERE code = ?", (forced,)
            ).fetchone():
                raise ValidationError(f"El código {forced} ya existe")
            next_number = int(db.meta_get(conn, TICKET_COUNTER, "1") or "1")
            if int(forced.split("-", 1)[1]) >= next_number:
                raise ValidationError(
                    f"{forced} todavía no es un hueco: el contador va por "
                    f"T-{next_number:04d}"
                )
            code = forced
        elif parent_row is not None:
            # Las tareas heredan el prefijo de la épica: T-0014.1, T-0014.2…
            code = _next_child_code(conn, parent_row["code"])
        else:
            prefix = (code_prefix or "T").strip().upper()
            if prefix not in ("T", SCRATCH_CODE_PREFIX.rstrip("-")):
                raise ValidationError(
                    f"Espacio de códigos inválido: {code_prefix!r} "
                    "(esperado T o S)"
                )
            code = allocate_code(conn, prefix)
        cur = conn.execute(
            "INSERT INTO tickets(code, title, body, kind, status, priority, project, path, "
            "created_by, assigned_to, parent_id, paths, isolation, resources, "
            "reversal, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, 'nuevo', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                code,
                title,
                body or "",
                kind,
                priority,
                project,
                path,
                created_by,
                (assigned_to or "").strip() or None,
                parent,
                json.dumps(scope, ensure_ascii=False) if scope else None,
                isolation,
                json.dumps(resources, ensure_ascii=False) if resources else None,
                json.dumps(reversal, ensure_ascii=False) if reversal else None,
                ts,
                ts,
            ),
        )
        ticket_id = int(cur.lastrowid or 0)
        for tag in tags:
            conn.execute(
                "INSERT OR IGNORE INTO tags(ticket_id, tag) VALUES (?, ?)",
                (ticket_id, tag),
            )
        event(
            conn,
            ticket_id,
            "crear",
            created_by,
            actor_type,
            session_id,
            None,
            "nuevo",
            f"Ticket creado ({kind}, {priority})",
        )

    return get_ticket(conn, code)


def update_ticket(
    conn: sqlite3.Connection,
    code: Any,
    title: Any = UNSET,
    body: Any = UNSET,
    kind: Any = UNSET,
    priority: Any = UNSET,
    project: Any = UNSET,
    path: Any = UNSET,
    tags: Any = UNSET,
    paths: Any = UNSET,
    isolation: Any = UNSET,
    resources: Any = UNSET,
    reversal: Any = UNSET,
    assigned_to: Any = UNSET,
    parent_id: Any = UNSET,
    actor: str = "agente",
    actor_type: str = "agente",
    session_id: str | None = None,
) -> dict[str, Any]:
    """Actualiza campos individuales de un ticket existente."""
    row = row_by_code(conn, code)
    changes: list[tuple[str, Any]] = []
    touched: list[str] = []

    if title is not UNSET:
        title = (title or "").strip()
        if not title:
            raise ValidationError("El título no puede quedar vacío")
        changes.append(("title", title))
        touched.append("title")
    if body is not UNSET:
        changes.append(("body", body or ""))
        touched.append("body")
    if kind is not UNSET:
        check_enum(kind, KINDS, "tipo")
        changes.append(("kind", kind))
        touched.append("kind")
    if priority is not UNSET:
        check_enum(priority, PRIORITIES, "prioridad")
        changes.append(("priority", priority))
        touched.append("priority")
    if project is not UNSET:
        changes.append(("project", project or None))
        touched.append("project")
    if path is not UNSET:
        changes.append(("path", path or None))
        touched.append("path")
    if paths is not UNSET:
        scope = clean_paths(paths)
        changes.append(
            ("paths", json.dumps(scope, ensure_ascii=False) if scope else None)
        )
        touched.append("paths")
    if isolation is not UNSET:
        changes.append(("isolation", clean_isolation(isolation)))
        touched.append("isolation")
    if resources is not UNSET:
        cleaned = clean_resources(resources)
        changes.append(
            ("resources", json.dumps(cleaned, ensure_ascii=False) if cleaned else None)
        )
        touched.append("resources")
    if reversal is not UNSET:
        cleaned_reversal = clean_reversal(reversal)
        changes.append(
            (
                "reversal",
                json.dumps(cleaned_reversal, ensure_ascii=False)
                if cleaned_reversal
                else None,
            )
        )
        touched.append("reversal")
    if assigned_to is not UNSET:
        agent = (assigned_to or "").strip() or None
        changes.append(("assigned_to", agent))
        touched.append("assigned_to")
    if parent_id is not UNSET:
        parent = validate_parent(conn, parent_id, row)
        changes.append(("parent_id", parent))
        touched.append("parent_id")

    if not changes and tags is UNSET:
        raise ValidationError("No se indicó ningún campo para actualizar")

    with db.tx(conn):
        if changes:
            changes.append(("updated_at", now()))
            sets = ", ".join(f"{name} = ?" for name, _ in changes)
            conn.execute(
                f"UPDATE tickets SET {sets} WHERE id = ?",
                (*[value for _, value in changes], row["id"]),
            )
        if tags is not UNSET:
            conn.execute("DELETE FROM tags WHERE ticket_id = ?", (row["id"],))
            for tag in clean_tags(tags):
                conn.execute(
                    "INSERT OR IGNORE INTO tags(ticket_id, tag) VALUES (?, ?)",
                    (row["id"], tag),
                )
            touched.append("tags")
        event(
            conn,
            row["id"],
            "actualizar",
            actor,
            actor_type,
            session_id,
            row["status"],
            row["status"],
            "Campos: " + ", ".join(touched),
        )

    return get_ticket(conn, code)


def renumber_plan(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    """Tareas con código plano que deberían ser ``T-XXXX.NN``.

    Solo se proponen hijos creados **después** del padre y que nunca pasaron
    por ``attach``: los tickets adoptados conservan su código a propósito
    (T-0008… bajo T-0015).
    """
    rows = conn.execute(
        "SELECT c.id, c.code, c.title, c.status, c.created_at, "
        "p.id AS parent_id, p.code AS parent_code, p.created_at AS parent_created "
        "FROM tickets c JOIN tickets p ON p.id = c.parent_id "
        "WHERE c.archived = 0 AND p.archived = 0 ORDER BY c.parent_id, c.id"
    ).fetchall()
    existing = {
        row["code"] for row in conn.execute("SELECT code FROM tickets")
    }
    adopted = {
        row["ticket_id"]
        for row in conn.execute(
            "SELECT DISTINCT ticket_id FROM events WHERE action = 'vincular'"
        )
    }

    groups: dict[int, list[Any]] = {}
    parents: dict[int, str] = {}
    for row in rows:
        if row["code"].startswith(row["parent_code"] + "."):
            continue
        if row["id"] in adopted:
            continue
        if row["created_at"] < row["parent_created"]:
            continue
        groups.setdefault(row["parent_id"], []).append(row)
        parents[row["parent_id"]] = row["parent_code"]

    plan: list[dict[str, Any]] = []
    for parent_id, items in groups.items():
        prefix = parents[parent_id] + "."
        number = 1
        for item in sorted(items, key=lambda row: row["id"]):
            while f"{prefix}{number:02d}" in existing:
                number += 1
            new_code = f"{prefix}{number:02d}"
            existing.add(new_code)
            plan.append(
                {
                    "id": item["id"],
                    "code": item["code"],
                    "new_code": new_code,
                    "title": item["title"],
                    "status": item["status"],
                }
            )
            number += 1
    return plan


def renumber_children(
    conn: sqlite3.Connection,
    apply: bool = False,
    actor: str = "usuario",
    actor_type: str = "usuario",
    session_id: str | None = None,
) -> list[dict[str, Any]]:
    """Renombra los códigos planos a ``padre.NN`` (simulación por defecto)."""
    plan = renumber_plan(conn)
    if not apply or not plan:
        return plan
    ts = now()
    with db.tx(conn):
        for item in plan:
            conn.execute(
                "UPDATE tickets SET code = ?, updated_at = ? WHERE id = ?",
                (item["new_code"], ts, item["id"]),
            )
            event(
                conn,
                item["id"],
                "renumerar",
                actor,
                actor_type,
                session_id,
                item["status"],
                item["status"],
                f"{item['code']} → {item['new_code']}",
            )
            # Registro estructurado del renumerado, independiente del texto.
            conn.execute(
                "INSERT OR IGNORE INTO links(ticket_id, kind, ref, created_at) "
                "VALUES (?, 'otro', ?, ?)",
                (
                    item["id"],
                    f"renumerado:{item['code']}->{item['new_code']}",
                    ts,
                ),
            )
    return plan


def attach_tickets(
    conn: sqlite3.Connection,
    parent: Any,
    codes: Any,
    actor: str = "agente",
    actor_type: str = "agente",
    session_id: str | None = None,
) -> list[dict[str, Any]]:
    """Convierte ``parent`` en épica y cuelga de ella los tickets indicados."""
    parent_row = row_by_code(conn, parent)
    if parent_row["archived"]:
        raise ValidationError(f"El padre {parent_row['code']} está archivado")
    if parent_row["parent_id"] is not None:
        raise ValidationError(
            f"{parent_row['code']} ya es una tarea; solo se admite un nivel"
        )
    if isinstance(codes, str):
        codes = [codes]
    rows = [row_by_code(conn, code) for code in codes]
    if not rows:
        raise ValidationError("Indica al menos una tarea para vincular")
    for child in rows:
        if child["id"] == parent_row["id"]:
            raise ValidationError("Un ticket no puede ser su propio padre")
        if child["archived"]:
            raise ValidationError(f"{child['code']} está archivado")
        if _has_children(conn, child["id"]):
            raise ValidationError(
                f"{child['code']} tiene tareas; no puede ser a la vez tarea"
            )

    ts = now()
    with db.tx(conn):
        for child in rows:
            if child["parent_id"] == parent_row["id"]:
                continue
            conn.execute(
                "UPDATE tickets SET parent_id = ?, updated_at = ? WHERE id = ?",
                (parent_row["id"], ts, child["id"]),
            )
            event(
                conn,
                child["id"],
                "vincular",
                actor,
                actor_type,
                session_id,
                child["status"],
                child["status"],
                f"Padre: {parent_row['code']}",
            )
    return [get_ticket(conn, child["code"]) for child in rows]


def detach_tickets(
    conn: sqlite3.Connection,
    codes: Any,
    actor: str = "agente",
    actor_type: str = "agente",
    session_id: str | None = None,
) -> list[dict[str, Any]]:
    """Devuelve los tickets a la raíz (los quita de su épica)."""
    if isinstance(codes, str):
        codes = [codes]
    rows = [row_by_code(conn, code) for code in codes]
    if not rows:
        raise ValidationError("Indica al menos una tarea para desvincular")
    ts = now()
    with db.tx(conn):
        for child in rows:
            if child["parent_id"] is None:
                continue
            conn.execute(
                "UPDATE tickets SET parent_id = NULL, updated_at = ? WHERE id = ?",
                (ts, child["id"]),
            )
            event(
                conn,
                child["id"],
                "desvincular",
                actor,
                actor_type,
                session_id,
                child["status"],
                child["status"],
                "Sin padre",
            )
    return [get_ticket(conn, child["code"]) for child in rows]


def fill_gaps(
    conn: sqlite3.Connection,
    apply: bool = False,
    actor: str = "usuario",
    actor_type: str = "usuario",
) -> list[dict[str, str]]:
    """Cubre los huecos de numeración con tickets genéricos cancelados.

    Un hueco es un ``T-XXXX`` que nunca llegó a existir. Se crea un ticket
    visible y ya cancelado, con la etiqueta ``hueco``, para que la secuencia
    quede completa y el número no vuelva a usarse: así una referencia antigua
    a ese código nunca apunta a trabajo nuevo. Devuelve el plan (simulación
    por defecto, igual que ``renumber``).
    """
    plan = [
        dict(item, action="cancelado") for item in missing_codes(conn)
    ]
    if not apply or not plan:
        return plan
    ts = now()
    # Un solo commit para todo el plan: si algo falla a mitad, no queda un
    # hueco visible en estado 'nuevo' ni un contador desincronizado.
    with db.tx(conn):
        for item in plan:
            ticket = create_ticket(
                conn,
                title="Hueco histórico de numeración",
                body=(
                    f"El código {item['code']} nunca tuvo ticket "
                    f"({item['reason']}). Se cubre con un ticket genérico "
                    "cancelado para que la secuencia no quede rota y el número "
                    "no se reutilice."
                ),
                kind="otro",
                priority="baja",
                tags=[TOMBSTONE_TAG],
                created_by=actor,
                actor_type=actor_type,
                code=item["code"],
            )
            conn.execute(
                "UPDATE tickets SET status = 'cancelado', completed_at = ?, "
                "updated_at = ?, resolution = ? WHERE id = ?",
                (
                    ts,
                    ts,
                    "Hueco de numeración cubierto: no existía ticket para "
                    "este código.",
                    ticket["id"],
                ),
            )
            event(
                conn,
                ticket["id"],
                "transicion",
                actor,
                actor_type,
                None,
                "nuevo",
                "cancelado",
                f"Hueco de numeración {item['code']} cubierto",
            )
    return plan
