"""Mutaciones y modificaciones de tickets en el almacén de datos."""

from __future__ import annotations

import json
import re
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any

from .. import db
from ..domain import (
    KINDS,
    PRIORITIES,
    SCRATCH_CODE_PREFIX,
    SCRATCH_COUNTER,
    TICKET_COUNTER,
    TOMBSTONE_TAG,
    NotFoundError,
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
    "purge_apply",
    "purge_plan",
    "reorg_apply",
    "reorg_plan",
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


REORG_ACTIONS = ("move", "renumber", "promote", "demote")


def _reorg_codes(codes: Any) -> list[Any]:
    if codes is None:
        return []
    if isinstance(codes, str):
        codes = [codes]
    return [code for code in codes if code is not None and str(code).strip()]


def _reorg_row(conn: sqlite3.Connection, code: Any) -> sqlite3.Row:
    try:
        return row_by_code(conn, code)
    except NotFoundError as exc:
        raise ValidationError(str(exc)) from exc


def _reorg_rows(
    conn: sqlite3.Connection, codes: Any
) -> list[sqlite3.Row]:
    """Resuelve y valida los tickets del plan (existentes y no archivados)."""
    rows: list[sqlite3.Row] = []
    seen: set[int] = set()
    for code in _reorg_codes(codes):
        row = _reorg_row(conn, code)
        if row["archived"]:
            raise ValidationError(f"{row['code']} está archivado")
        if row["id"] in seen:
            raise ValidationError(
                f"El ticket {row['code']} aparece más de una vez en el plan"
            )
        seen.add(row["id"])
        rows.append(row)
    return sorted(rows, key=lambda row: row["id"])


def _reorg_parent(
    conn: sqlite3.Connection, parent: Any, moving_ids: set[int]
) -> sqlite3.Row:
    """Valida el padre destino: existe, no archivado y es raíz."""
    if parent is None or not str(parent).strip():
        raise ValidationError("Indica un padre para la reorganización")
    parent_row = _reorg_row(conn, parent)
    if parent_row["archived"]:
        raise ValidationError(f"El padre {parent_row['code']} está archivado")
    if parent_row["id"] in moving_ids:
        raise ValidationError(
            f"{parent_row['code']} no puede ser su propio padre ni formar "
            "parte del plan"
        )
    if parent_row["parent_id"] is not None:
        if parent_row["parent_id"] in moving_ids:
            raise ValidationError(
                "No se puede mover un ticket a su propia descendencia "
                f"({parent_row['code']})"
            )
        raise ValidationError(
            f"{parent_row['code']} ya es una tarea; solo se admite un nivel "
            "(épica → tarea)"
        )
    return parent_row


def _reorg_item(
    row: sqlite3.Row, new_code: str, parent_code: str | None
) -> dict[str, Any]:
    return {
        "id": row["id"],
        "code": row["code"],
        "new_code": new_code,
        "title": row["title"],
        "status": row["status"],
        "parent_code": parent_code,
    }


def _reorg_finish(
    action: str,
    items: list[dict[str, Any]],
    warnings: list[str],
) -> dict[str, Any]:
    new_codes = [item["new_code"] for item in items]
    if len(new_codes) != len(set(new_codes)):
        raise ValidationError("El plan genera códigos duplicados")
    return {"action": action, "items": items, "warnings": warnings}


def _plan_move(
    conn: sqlite3.Connection,
    rows: list[sqlite3.Row],
    parent: Any,
    renumber: bool | None,
) -> dict[str, Any]:
    if renumber is None:
        raise ValidationError(
            "Indica renumber=True o renumber=False para mover: no se adivina "
            "si el código debe cambiar"
        )
    parent_row = _reorg_parent(conn, parent, {row["id"] for row in rows})
    for row in rows:
        if _has_children(conn, row["id"]):
            raise ValidationError(
                f"{row['code']} tiene tareas; una épica no puede convertirse "
                "en tarea"
            )

    used = {
        row["code"] for row in conn.execute("SELECT code FROM tickets")
    } - {row["code"] for row in rows}
    warnings: list[str] = []
    items: list[dict[str, Any]] = []
    number = 1
    for row in rows:
        if renumber:
            while f"{parent_row['code']}.{number:02d}" in used:
                number += 1
            new_code = f"{parent_row['code']}.{number:02d}"
            used.add(new_code)
            number += 1
        else:
            new_code = row["code"]
            if not new_code.startswith(parent_row["code"] + "."):
                warnings.append(
                    f"{row['code']} conserva su código plano bajo "
                    f"{parent_row['code']}"
                )
        if row["parent_id"] == parent_row["id"]:
            warnings.append(
                f"{row['code']} ya está bajo {parent_row['code']}"
            )
        items.append(_reorg_item(row, new_code, parent_row["code"]))
    return _reorg_finish("move", items, warnings)


def _child_rows(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT c.id, c.code, c.title, c.status, c.created_at, c.parent_id, "
        "p.code AS parent_code, p.created_at AS parent_created, "
        "p.archived AS parent_archived "
        "FROM tickets c JOIN tickets p ON p.id = c.parent_id "
        "WHERE c.archived = 0"
    ).fetchall()


def _plan_renumber(
    conn: sqlite3.Connection,
    rows: list[sqlite3.Row],
    include_adopted: bool,
) -> dict[str, Any]:
    adopted = {
        row["ticket_id"]
        for row in conn.execute(
            "SELECT DISTINCT ticket_id FROM events WHERE action = 'vincular'"
        )
    }
    children = {row["id"]: row for row in _child_rows(conn)}
    if rows:
        selected: list[sqlite3.Row] = []
        for row in rows:
            child = children.get(row["id"])
            if child is None:
                raise ValidationError(
                    f"{row['code']} no es una tarea; no hay nada que renumerar"
                )
            if child["parent_archived"]:
                raise ValidationError(
                    f"El padre de {row['code']} está archivado"
                )
            selected.append(child)
    else:
        selected = list(children.values())

    def eligible(row: sqlite3.Row) -> bool:
        if row["parent_archived"]:
            return False
        if row["code"].startswith(row["parent_code"] + "."):
            return False
        if row["id"] in adopted:
            return include_adopted
        return row["created_at"] >= row["parent_created"]

    candidates = [row for row in selected if eligible(row)]
    used = {
        row["code"] for row in conn.execute("SELECT code FROM tickets")
    } - {row["code"] for row in candidates}
    groups: dict[int, list[sqlite3.Row]] = {}
    for row in candidates:
        groups.setdefault(row["parent_id"], []).append(row)

    items: list[dict[str, Any]] = []
    for parent_id in sorted(groups):
        group = sorted(groups[parent_id], key=lambda row: row["id"])
        prefix = group[0]["parent_code"] + "."
        number = 1
        for row in group:
            while f"{prefix}{number:02d}" in used:
                number += 1
            new_code = f"{prefix}{number:02d}"
            used.add(new_code)
            items.append(
                _reorg_item(row, new_code, row["parent_code"])
            )
            number += 1
    warnings: list[str] = []
    if rows and not items:
        warnings.append("No hay tareas que renumerar con esos filtros")
    return _reorg_finish("renumber", items, warnings)


def _plan_promote(
    conn: sqlite3.Connection, rows: list[sqlite3.Row]
) -> dict[str, Any]:
    warnings: list[str] = []
    items: list[dict[str, Any]] = []
    for row in rows:
        if row["parent_id"] is None:
            if _has_children(conn, row["id"]):
                warnings.append(f"{row['code']} ya es una épica con tareas")
            else:
                warnings.append(f"{row['code']} ya es una épica potencial")
        else:
            parent = row_by_code(conn, row["parent_id"])
            warnings.append(
                f"{row['code']} se desvincula de {parent['code']} para poder "
                "tener tareas"
            )
        items.append(_reorg_item(row, row["code"], None))
    return _reorg_finish("promote", items, warnings)


def _plan_demote(
    conn: sqlite3.Connection,
    rows: list[sqlite3.Row],
    children_to: Any,
    parent: Any,
) -> dict[str, Any]:
    if children_to is UNSET:
        raise ValidationError(
            "Indica children_to: otra épica o None para dejar las tareas en "
            "la raíz"
        )
    moving_ids = {row["id"] for row in rows}
    target: sqlite3.Row | None = None
    if children_to is not None and str(children_to).strip():
        target = _reorg_row(conn, children_to)
        if target["archived"]:
            raise ValidationError(
                f"El destino {target['code']} está archivado"
            )
        if target["id"] in moving_ids:
            raise ValidationError(
                "El destino de las tareas no puede ser uno de los tickets "
                "degradados"
            )
        if target["parent_id"] is not None:
            raise ValidationError(
                f"{target['code']} ya es una tarea; solo se admite un nivel "
                "(épica → tarea)"
            )
    dest_parent = None
    if parent is not None and str(parent).strip():
        dest_parent = _reorg_parent(conn, parent, moving_ids)

    items: list[dict[str, Any]] = []
    warnings: list[str] = []
    for row in rows:
        children = conn.execute(
            "SELECT * FROM tickets WHERE parent_id = ? ORDER BY id",
            (row["id"],),
        ).fetchall()
        if not children:
            raise ValidationError(
                f"{row['code']} no tiene tareas que reasignar"
            )
        if target is not None and any(
            child["id"] == target["id"] for child in children
        ):
            raise ValidationError(
                f"{target['code']} es una tarea de {row['code']}; no puede "
                "ser el destino"
            )
        parent_code = dest_parent["code"] if dest_parent else None
        items.append(_reorg_item(row, row["code"], parent_code))
        for child in children:
            items.append(
                _reorg_item(
                    child,
                    child["code"],
                    target["code"] if target else None,
                )
            )
        if target is None:
            warnings.append(
                f"{row['code']}: {len(children)} tarea(s) pasan a la raíz"
            )
    return _reorg_finish("demote", items, warnings)


def reorg_plan(
    conn: sqlite3.Connection,
    action: str,
    codes: Any = None,
    parent: Any = None,
    children_to: Any = UNSET,
    include_adopted: bool = False,
    renumber: bool | None = None,
) -> dict[str, Any]:
    """Plan puro de reorganización: no escribe en la base de datos.

    ``action`` es ``move`` (cambiar de épica), ``renumber`` (códigos
    ``padre.NN``), ``promote`` (tarea → raíz/épica) o ``demote`` (épica →
    tarea, repartiendo sus hijos). Devuelve ``action``, ``items`` y
    ``warnings``; ``apply`` vive en la tarea que ejecuta el plan.
    """
    if action not in REORG_ACTIONS:
        raise ValidationError(
            f"Acción de reorganización desconocida: {action}. "
            f"Usa una de: {', '.join(REORG_ACTIONS)}"
        )
    rows = _reorg_rows(conn, codes)
    if action != "renumber" and not rows:
        raise ValidationError("Indica al menos un ticket para reorganizar")
    if action == "move":
        return _plan_move(conn, rows, parent, renumber)
    if action == "renumber":
        return _plan_renumber(conn, rows, include_adopted)
    if action == "promote":
        return _plan_promote(conn, rows)
    return _plan_demote(conn, rows, children_to, parent)


_REORG_LINK_KINDS = ("depende", "relacionado")


def _reorg_parent_id(
    conn: sqlite3.Connection, parent_code: str | None
) -> int | None:
    if parent_code is None:
        return None
    return row_by_code(conn, parent_code)["id"]


def _reorg_rewrite_references(
    conn: sqlite3.Connection, old_code: str, new_code: str
) -> dict[str, int]:
    """Reapunta referencias estructuradas y limpia duplicados obsoletos.

    ``UPDATE OR IGNORE`` deja intacta la fila que choca con una referencia
    ya existente al código nuevo; después se borra el resto viejo para no
    reventar por ``UNIQUE`` ni dejar referencias huérfanas.
    """
    marks = ",".join("?" for _ in _REORG_LINK_KINDS)
    links = conn.execute(
        f"UPDATE OR IGNORE links SET ref = ? "
        f"WHERE kind IN ({marks}) AND ref = ?",
        (new_code, *_REORG_LINK_KINDS, old_code),
    ).rowcount
    leftovers = conn.execute(
        f"DELETE FROM links WHERE kind IN ({marks}) AND ref = ?",
        (*_REORG_LINK_KINDS, old_code),
    ).rowcount
    evidence = conn.execute(
        "UPDATE OR IGNORE memory_evidence SET ref = ? "
        "WHERE kind = 'ticket' AND ref = ?",
        (new_code, old_code),
    ).rowcount
    evidence += conn.execute(
        "DELETE FROM memory_evidence WHERE kind = 'ticket' AND ref = ?",
        (old_code,),
    ).rowcount
    memories = conn.execute(
        "UPDATE memories SET ticket_id = ? WHERE ticket_id = ?",
        (new_code, old_code),
    ).rowcount
    return {
        "links": links + leftovers,
        "evidence": evidence,
        "memories": memories,
    }


def reorg_apply(
    conn: sqlite3.Connection,
    action: str,
    codes: Any = None,
    parent: Any = None,
    children_to: Any = UNSET,
    include_adopted: bool = False,
    renumber: bool | None = None,
    actor: str = "agente",
    actor_type: str = "agente",
    session_id: str | None = None,
) -> dict[str, Any]:
    """Aplica un plan de :func:`reorg_plan` en una transacción atómica.

    Actualiza códigos y padres, reapunta las referencias estructuradas
    (``links``, ``memory_evidence`` y ``memories.ticket_id``) y registra los
    eventos de renumerado y cambio de padre. Los eventos, comentarios y
    enlaces históricos (``kind='otro'``) no se reescriben. Si algo falla, el
    rollback revierte todo y el error se propaga.
    """
    plan = reorg_plan(
        conn,
        action,
        codes,
        parent=parent,
        children_to=children_to,
        include_adopted=include_adopted,
        renumber=renumber,
    )
    items = plan["items"]
    references = {"links": 0, "evidence": 0, "memories": 0}
    if not items:
        return {**plan, "references": references, "events": 0}

    events = 0
    ts = now()
    with db.tx(conn):
        # Parent anterior y estado real: se captura antes de tocar códigos.
        previous = {
            item["id"]: conn.execute(
                "SELECT parent_id, status FROM tickets WHERE id = ?",
                (item["id"],),
            ).fetchone()
            for item in items
        }
        old_parent_codes = {
            parent_id: conn.execute(
                "SELECT code FROM tickets WHERE id = ?", (parent_id,)
            ).fetchone()["code"]
            for parent_id in {
                row["parent_id"]
                for row in previous.values()
                if row["parent_id"] is not None
            }
        }
        parent_ids = {
            item["id"]: _reorg_parent_id(conn, item["parent_code"])
            for item in items
        }

        # (a) Renombrado en dos fases: un código temporal evita colisiones
        # UNIQUE cuando los códigos finales chocan entre sí o con los viejos.
        renamed = [
            item for item in items if item["new_code"] != item["code"]
        ]
        for item in renamed:
            conn.execute(
                "UPDATE tickets SET code = ? WHERE id = ?",
                (f"__reorg__{item['id']}__", item["id"]),
            )
        for item in renamed:
            conn.execute(
                "UPDATE tickets SET code = ? WHERE id = ?",
                (item["new_code"], item["id"]),
            )

        # (b) Parent_id resuelto desde el padre destino del plan.
        for item in items:
            conn.execute(
                "UPDATE tickets SET parent_id = ? WHERE id = ?",
                (parent_ids[item["id"]], item["id"]),
            )

        # (c) Solo referencias estructuradas; lo histórico se conserva.
        for item in renamed:
            touched = _reorg_rewrite_references(
                conn, item["code"], item["new_code"]
            )
            for key, value in touched.items():
                references[key] += value

        # (d) Eventos: renumerado y cambios reales de padre.
        for item in items:
            row = previous[item["id"]]
            status = item["status"]
            if item["new_code"] != item["code"]:
                event(
                    conn,
                    item["id"],
                    "renumerar",
                    actor,
                    actor_type,
                    session_id,
                    status,
                    status,
                    f"{item['code']} → {item['new_code']}",
                )
                events += 1
            old_parent = row["parent_id"]
            new_parent = parent_ids[item["id"]]
            if old_parent != new_parent:
                if new_parent is None:
                    event(
                        conn,
                        item["id"],
                        "desvincular",
                        actor,
                        actor_type,
                        session_id,
                        status,
                        status,
                        f"desde {old_parent_codes.get(old_parent, old_parent)}",
                    )
                else:
                    event(
                        conn,
                        item["id"],
                        "vincular",
                        actor,
                        actor_type,
                        session_id,
                        status,
                        status,
                        f"bajo {item['parent_code']}",
                    )
                events += 1

        # (e) Solo cambian code, parent_id y updated_at.
        for item in items:
            conn.execute(
                "UPDATE tickets SET updated_at = ? WHERE id = ?",
                (ts, item["id"]),
            )

    return {
        "action": plan["action"],
        "items": items,
        "warnings": plan["warnings"],
        "references": references,
        "events": events,
    }


def renumber_plan(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    """Tareas con código plano que deberían ser ``T-XXXX.NN``.

    Solo se proponen hijos creados **después** del padre y que nunca pasaron
    por ``attach``: los tickets adoptados conservan su código a propósito
    (T-0008… bajo T-0015).
    """
    return reorg_plan(conn, "renumber")["items"]


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


# ── Purga de tickets cancelados ──────────────────────────────────────────

PURGE_STATUS = "cancelado"
_PURGE_ROOT = re.compile(r"^T-(\d+)$")


def _purge_rows(
    conn: sqlite3.Connection, codes: Any
) -> list[sqlite3.Row]:
    """Filas únicas de los códigos indicados, en orden de id."""
    if isinstance(codes, str):
        codes = [codes]
    codes = list(codes or [])
    if not codes:
        raise ValidationError("Indica al menos un ticket para purgar")
    rows: dict[int, sqlite3.Row] = {}
    for code in codes:
        row = row_by_code(conn, code)
        rows[row["id"]] = row
    return [rows[key] for key in sorted(rows)]


def _purge_reasons(
    conn: sqlite3.Connection, row: sqlite3.Row
) -> list[str]:
    """Motivos por los que un ticket no se puede purgar (vacío si se puede)."""
    reasons: list[str] = []
    if row["status"] != PURGE_STATUS:
        reasons.append(
            f"{row['code']}: status '{row['status']}' no es {PURGE_STATUS}; "
            "cancélalo antes de purgarlo"
        )
    children = conn.execute(
        "SELECT code FROM tickets WHERE parent_id = ? ORDER BY code",
        (row["id"],),
    ).fetchall()
    if children:
        reasons.append(
            f"{row['code']}: tiene tareas ({', '.join(child['code'] for child in children)}); "
            "purgarlas o moverlas primero"
        )
    link_kinds = [
        link["kind"]
        for link in conn.execute(
            "SELECT DISTINCT kind FROM links WHERE ref = ?", (row["code"],)
        )
    ]
    if link_kinds:
        reasons.append(
            f"{row['code']}: referenciado por enlaces ({', '.join(link_kinds)}); "
            "quita la referencia antes de purgarlo"
        )
    evidence = conn.execute(
        "SELECT COUNT(*) FROM memory_evidence "
        "WHERE kind = 'ticket' AND ref = ?",
        (row["code"],),
    ).fetchone()[0]
    if evidence:
        reasons.append(
            f"{row['code']}: es evidencia de {evidence} memoria(s); "
            "actualiza la memoria antes de purgarlo"
        )
    memories = conn.execute(
        "SELECT COUNT(*) FROM memories WHERE ticket_id = ?", (row["code"],)
    ).fetchone()[0]
    if memories:
        reasons.append(
            f"{row['code']}: es ticket_id de {memories} memoria(s); "
            "desvincula la memoria antes de purgarlo"
        )
    return reasons


def _purge_counter(
    conn: sqlite3.Connection, rows: list[sqlite3.Row]
) -> tuple[dict[str, int], list[str]]:
    """Proyección del contador: solo baja al liberar un sufijo de raíces."""
    before = int(db.meta_get(conn, TICKET_COUNTER, "1") or "1")
    counter = {"before": before, "after": before}
    released = sorted(
        int(match.group(1))
        for row in rows
        if (match := _PURGE_ROOT.match(row["code"]))
    )
    if not released:
        return counter, []
    purged_ids = {row["id"] for row in rows}
    remaining = [
        int(match.group(1))
        for row in conn.execute("SELECT id, code FROM tickets")
        if row["id"] not in purged_ids
        and (match := _PURGE_ROOT.match(row["code"]))
    ]
    highest = max(remaining, default=0)
    if released[0] > highest:
        counter["after"] = min(before, released[0])
        return counter, []
    return counter, [
        "El contador no baja: quedan raíces por encima del sufijo liberado; "
        "revisa los huecos con 'taiki repair-gaps'"
    ]


def purge_plan(
    conn: sqlite3.Connection, codes: Any
) -> dict[str, Any]:
    """Plan puro de purga: no escribe y rechaza cualquier referencia viva.

    Solo admite tickets ``cancelado`` sin hijos, sin enlaces que apunten a su
    código y sin memorias o evidencias que lo referencien. Devuelve ``items``,
    la proyección del contador y ``warnings``.
    """
    rows = _purge_rows(conn, codes)
    failures: list[str] = []
    for row in rows:
        failures.extend(_purge_reasons(conn, row))
    if failures:
        raise ValidationError(
            "No se puede purgar:\n- " + "\n- ".join(failures)
        )
    counter, warnings = _purge_counter(conn, rows)
    items = [
        {
            "id": row["id"],
            "code": row["code"],
            "title": row["title"],
            "status": row["status"],
        }
        for row in rows
    ]
    return {"items": items, "counter": counter, "warnings": warnings}


def _database_file(conn: sqlite3.Connection) -> Path:
    """Ruta real de la DB principal; ``:memory:`` no se puede respaldar."""
    rows = conn.execute("PRAGMA database_list").fetchall()
    raw = next((row[2] for row in rows if row[1] == "main"), "")
    path = Path(raw).expanduser() if raw else None
    if path is None or not path.is_file():
        raise ValidationError(
            "purge --apply necesita una base de datos en fichero para hacer "
            "el backup obligatorio; las bases :memory: no se pueden purgar"
        )
    return path


def _purge_backup(path: Path) -> Path:
    """Copia consistente de la DB antes de borrar; sin copia no hay purga."""
    target = (
        path.parent / "backups" / f"taiki-{datetime.now():%Y%m%d-%H%M%S-%f}.db"
    )
    from ..workspace import _copy_database

    try:
        _copy_database(path, target)
    except (OSError, sqlite3.Error) as exc:
        raise ValidationError(
            f"No se pudo crear el backup en {target}: {exc}. No se purga nada"
        ) from exc
    if not target.is_file():
        raise ValidationError(
            f"El backup {target} no se creó. No se purga nada"
        )
    return target


def purge_apply(
    conn: sqlite3.Connection,
    codes: Any,
    actor: str = "usuario",
    actor_type: str = "usuario",
    session_id: str | None = None,
) -> dict[str, Any]:
    """Purga tickets cancelados con backup previo y guardrails.

    Calcula el plan (si algo falla no escribe), copia la DB a
    ``<dir_db>/backups/taiki-<timestamp>.db`` y borra en una sola transacción
    el ticket y sus eventos, comentarios, enlaces y etiquetas. Los triggers de
    FTS limpian el índice; después se comprueba su integridad. El contador solo
    baja cuando se libera un sufijo de raíces (ver :func:`_purge_counter`).
    """
    plan = purge_plan(conn, codes)
    if not plan["items"]:
        return {**plan, "backup": None}
    path = _database_file(conn)
    backup = _purge_backup(path)
    ticket_ids = [item["id"] for item in plan["items"]]
    with db.tx(conn):
        for ticket_id in ticket_ids:
            for table in ("events", "comments", "links", "tags"):
                conn.execute(
                    f"DELETE FROM {table} WHERE ticket_id = ?", (ticket_id,)
                )
            conn.execute("DELETE FROM tickets WHERE id = ?", (ticket_id,))
        conn.execute(
            "INSERT INTO tickets_fts(tickets_fts) VALUES('integrity-check')"
        )
        if plan["counter"]["after"] != plan["counter"]["before"]:
            db.meta_set(
                conn, TICKET_COUNTER, str(plan["counter"]["after"])
            )
        db.meta_set(
            conn,
            "last_purge",
            json.dumps(
                {
                    "ts": now(),
                    "actor": actor,
                    "actor_type": actor_type,
                    "session_id": session_id,
                    "codes": [item["code"] for item in plan["items"]],
                    "backup": str(backup),
                },
                ensure_ascii=False,
            ),
        )
    return {**plan, "backup": str(backup)}
