"""Consultas y lectura de tickets en el almacén de datos."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from functools import lru_cache
from typing import Any, Iterable

from .. import db
from ..domain import (
    KINDS,
    PARENT_CONFIRMED_STATUSES,
    PARENT_DONE_STATUSES,
    PRIORITIES,
    SCRATCH_TAG,
    STATUSES,
    TICKET_COUNTER,
)
from ..util import check_enum, clean_tags, fts_match, search_tokens
from .common import UNSET, row_by_code, row_to_ticket

__all__ = [
    "activity_by_day",
    "count_tickets",
    "get_ticket",
    "hidden_codes",
    "list_tickets",
    "missing_codes",
    "next_ticket",
    "search_codes",
    "subtask_progress",
]


def _history_section(
    conn: sqlite3.Connection,
    table: str,
    columns: str,
    ticket_id: int,
    limit: Any,
) -> tuple[list[dict[str, Any]], int | None]:
    """Carga una sección de historial, opcionalmente acotada en SQL.

    Devuelve ``(filas, total)``; ``total`` solo se informa cuando se recortó.
    Con ``limit`` nulo o ``<= 0`` se devuelve el historial completo.
    """
    try:
        maximum = int(limit) if limit is not None else 0
    except (TypeError, ValueError):
        maximum = 0
    if maximum <= 0:
        rows = conn.execute(
            f"SELECT {columns} FROM {table} WHERE ticket_id = ? ORDER BY id",
            (ticket_id,),
        )
        return [dict(row) for row in rows], None
    # Se pide una fila de más: si no aparece, no hubo recorte y no hace falta
    # el COUNT. Si aparece, el total se consulta una sola vez.
    rows = conn.execute(
        f"SELECT {columns} FROM (SELECT {columns}, id FROM {table} "
        "WHERE ticket_id = ? ORDER BY id DESC LIMIT ?) ORDER BY id",
        (ticket_id, maximum + 1),
    ).fetchall()
    if len(rows) <= maximum:
        return [dict(row) for row in rows], None
    total = conn.execute(
        f"SELECT COUNT(*) FROM {table} WHERE ticket_id = ?", (ticket_id,)
    ).fetchone()[0]
    return [dict(row) for row in rows[1:]], total


def _limited_section(
    conn: sqlite3.Connection,
    sql: str,
    params: tuple[Any, ...],
    count_sql: str,
    count_params: tuple[Any, ...],
    limit: Any,
) -> tuple[list[dict[str, Any]], int | None]:
    """Igual que ``_history_section`` pero para consultas ya construidas."""
    try:
        maximum = int(limit) if limit is not None else 0
    except (TypeError, ValueError):
        maximum = 0
    if maximum <= 0:
        return [dict(row) for row in conn.execute(sql, params)], None
    rows = conn.execute(sql + " LIMIT ?", (*params, maximum + 1)).fetchall()
    if len(rows) <= maximum:
        return [dict(row) for row in rows], None
    total = conn.execute(count_sql, count_params).fetchone()[0]
    return [dict(row) for row in rows[1:]], total


def _subtask_progress(
    conn: sqlite3.Connection, ticket_id: int
) -> dict[str, int]:
    """Progreso completo de las tareas, sin depender de su límite de carga."""
    row = conn.execute(
        "SELECT COUNT(*) AS total, "
        "SUM(CASE WHEN status IN ("
        + ", ".join("?" for _ in PARENT_DONE_STATUSES)
        + ") THEN 1 ELSE 0 END) AS done, "
        "SUM(CASE WHEN status IN ("
        + ", ".join("?" for _ in PARENT_CONFIRMED_STATUSES)
        + ") THEN 1 ELSE 0 END) AS confirmed "
        "FROM tickets WHERE parent_id = ?",
        (*PARENT_DONE_STATUSES, *PARENT_CONFIRMED_STATUSES, ticket_id),
    ).fetchone()
    return {
        "done": row["done"] or 0,
        "confirmed": row["confirmed"] or 0,
        "total": row["total"] or 0,
    }


def get_ticket(
    conn: sqlite3.Connection,
    code: Any,
    *,
    history_limit: Any = None,
    links_limit: Any = None,
    subtasks_limit: Any = None,
    commits_limit: Any = None,
    fields: Iterable[str] | None = None,
) -> dict[str, Any]:
    """Recupera un ticket completo con etiquetas, comentarios, eventos y enlaces.

    Los límites acotan cada sección en la propia consulta y solo cuentan el
    total cuando hubo recorte. ``fields`` proyecta las columnas del ticket
    (``code`` se conserva siempre).
    """
    row = row_by_code(conn, code)
    ticket = row_to_ticket(row)
    ticket["tags"] = [
        item["tag"]
        for item in conn.execute(
            "SELECT tag FROM tags WHERE ticket_id = ? ORDER BY tag", (row["id"],)
        )
    ]
    ticket["comments"], comments_total = _history_section(
        conn, "comments", "ts, author, body", row["id"], history_limit
    )
    if comments_total is not None and comments_total > len(ticket["comments"]):
        ticket["comments_total"] = comments_total
    ticket["events"], events_total = _history_section(
        conn,
        "events",
        "ts, actor, actor_type, action, from_status, to_status, note, session_id",
        row["id"],
        history_limit,
    )
    if events_total is not None and events_total > len(ticket["events"]):
        ticket["events_total"] = events_total
    ticket["links"], links_total = _limited_section(
        conn,
        "SELECT kind, ref, created_at FROM ("
        "SELECT kind, ref, created_at, id FROM links "
        "WHERE ticket_id = ? ORDER BY id DESC) ORDER BY id",
        (row["id"],),
        "SELECT COUNT(*) FROM links WHERE ticket_id = ?",
        (row["id"],),
        links_limit,
    )
    if links_total is not None:
        ticket["links_total"] = links_total
        # Los enlaces se recortaron: los commits se consultan aparte para que
        # commits_limit sea independiente de links_limit.
        commit_rows = conn.execute(
            "SELECT ref FROM links WHERE ticket_id = ? AND kind = 'commit' "
            "ORDER BY id",
            (row["id"],),
        )
    else:
        commit_rows = [
            link for link in ticket["links"] if link["kind"] == "commit"
        ]
    commits: list[str] = []
    for link in commit_rows:
        ref = link["ref"]
        if any(
            ref == known or ref.startswith(known) or known.startswith(ref)
            for known in commits
        ):
            continue
        commits.append(ref)
    try:
        commits_maximum = int(commits_limit) if commits_limit is not None else 0
    except (TypeError, ValueError):
        commits_maximum = 0
    if commits_maximum > 0 and len(commits) > commits_maximum:
        ticket["commits_total"] = len(commits)
        commits = commits[-commits_maximum:]
    ticket["commits"] = commits
    ticket["committed"] = bool(commits)
    if ticket["parent_id"]:
        parent = conn.execute(
            "SELECT code FROM tickets WHERE id = ?", (ticket["parent_id"],)
        ).fetchone()
        ticket["parent_code"] = parent["code"] if parent else None
    else:
        ticket["parent_code"] = None
    ticket["subtasks"], subtasks_total = _limited_section(
        conn,
        "SELECT code, title, status, priority, kind FROM ("
        "SELECT code, title, status, priority, kind, id FROM tickets "
        "WHERE parent_id = ? ORDER BY id DESC) ORDER BY id",
        (row["id"],),
        "SELECT COUNT(*) FROM tickets WHERE parent_id = ?",
        (row["id"],),
        subtasks_limit,
    )
    if subtasks_total is not None:
        ticket["subtasks_total"] = subtasks_total
        ticket["progress"] = _subtask_progress(conn, row["id"])
    else:
        ticket["progress"] = {
            "done": sum(
                1
                for sub in ticket["subtasks"]
                if sub["status"] in PARENT_DONE_STATUSES
            ),
            "confirmed": sum(
                1
                for sub in ticket["subtasks"]
                if sub["status"] in PARENT_CONFIRMED_STATUSES
            ),
            "total": len(ticket["subtasks"]),
        }
    if fields:
        keep = {str(name) for name in fields} | {"code"}
        return {key: value for key, value in ticket.items() if key in keep}
    return ticket


ORDER_MODES = ("code", "priority")
ORDER_BY_PRIORITY = (
    " ORDER BY CASE t.priority "
    "WHEN 'urgente' THEN 0 WHEN 'alta' THEN 1 WHEN 'media' THEN 2 ELSE 3 END, "
    "t.created_at ASC, t.id ASC"
)

_TICKET_COLUMNS: tuple[str, ...] | None = None


@lru_cache(maxsize=8)
def _lean_columns(columns: tuple[str, ...]) -> str:
    return ", ".join(
        f"t.{name}" for name in columns if name not in ("body", "resolution")
    )


def _lean_projection(conn: sqlite3.Connection) -> str:
    """Columnas del listado lean, sin releer el esquema en cada llamada."""
    global _TICKET_COLUMNS
    if _TICKET_COLUMNS is None:
        _TICKET_COLUMNS = tuple(
            row["name"] for row in conn.execute("PRAGMA table_info(tickets)")
        )
    return _lean_columns(_TICKET_COLUMNS)


@dataclass(frozen=True)
class TicketFilter:
    """Filtros de list/count en un solo objeto."""

    status: str | None = None
    statuses: Iterable[str] | None = None
    kind: str | None = None
    priority: str | None = None
    project: str | None = None
    assignee: str | None = None
    query: str | None = None
    include_archived: bool = False
    tag: str | None = None
    tags: Iterable[str] | None = None
    parent_id: Any = UNSET
    roots_only: bool = False
    leaves_only: bool = False
    include_scratch: bool = False

    def build(self, conn: sqlite3.Connection) -> tuple[str, list[str], list[Any]]:
        parent_pk = UNSET
        if self.parent_id is not UNSET:
            parent_pk = (
                None
                if self.parent_id is None
                else row_by_code(conn, self.parent_id)["id"]
            )
        return _build_filters(self, parent_pk)


def _build_filters(
    filt: TicketFilter,
    parent_pk: Any = UNSET,
) -> tuple[str, list[str], list[Any]]:
    """Devuelve (join, condiciones WHERE, parámetros) para list y count."""
    statuses = filt.statuses
    if statuses is None and filt.status:
        statuses = [filt.status]
    statuses = list(statuses) if statuses else []
    for item in statuses:
        check_enum(item, STATUSES, "estado")
    check_enum(filt.kind, KINDS, "tipo")
    check_enum(filt.priority, PRIORITIES, "prioridad")

    join = ""
    where: list[str] = []
    params: list[Any] = []

    if statuses:
        where.append(f"t.status IN ({', '.join('?' for _ in statuses)})")
        params.extend(statuses)
    if filt.kind:
        where.append("t.kind = ?")
        params.append(filt.kind)
    if filt.priority:
        where.append("t.priority = ?")
        params.append(filt.priority)
    if filt.project:
        where.append("t.project = ?")
        params.append(filt.project)
    if filt.assignee:
        where.append("t.assigned_to = ?")
        params.append(filt.assignee)

    filter_tags: list[str] = []
    if filt.tag:
        filter_tags.extend(clean_tags(filt.tag))
    if filt.tags:
        filter_tags.extend(clean_tags(filt.tags))
    if filter_tags:
        filter_tags = list(dict.fromkeys(filter_tags))
        placeholders = ", ".join("?" for _ in filter_tags)
        where.append(
            "EXISTS (SELECT 1 FROM tags tg WHERE tg.ticket_id = t.id "
            f"AND tg.tag IN ({placeholders}))"
        )
        params.extend(filter_tags)

    if filt.query:
        match = fts_match(filt.query)
        if match:
            join = " JOIN tickets_fts ON tickets_fts.rowid = t.id"
            where.append("tickets_fts MATCH ?")
            params.append(match)
    if parent_pk is not UNSET:
        if parent_pk is None:
            where.append("t.parent_id IS NULL")
        else:
            where.append("t.parent_id = ?")
            params.append(parent_pk)
    elif filt.roots_only:
        where.append("t.parent_id IS NULL")
    if filt.leaves_only:
        where.append(
            "NOT EXISTS (SELECT 1 FROM tickets ch "
            "WHERE ch.parent_id = t.id AND ch.archived = 0)"
        )
    if not filt.include_archived:
        where.append("t.archived = 0")
    if not filt.include_scratch:
        where.append(
            "NOT EXISTS (SELECT 1 FROM tags st WHERE st.ticket_id = t.id "
            "AND st.tag = '_scratch')"
        )

    return join, where, params


def list_tickets(
    conn: sqlite3.Connection,
    status: str | None = None,
    statuses: Iterable[str] | None = None,
    kind: str | None = None,
    priority: str | None = None,
    project: str | None = None,
    assignee: str | None = None,
    query: str | None = None,
    include_archived: bool = False,
    limit: int | None = 50,
    tag: str | None = None,
    tags: Iterable[str] | None = None,
    offset: int = 0,
    order: str = "code",
    parent_id: Any = UNSET,
    roots_only: bool = False,
    leaves_only: bool = False,
    include_scratch: bool = False,
    detail: bool = False,
    filters: TicketFilter | None = None,
) -> list[dict[str, Any]]:
    """Lista tickets con filtros, paginación, orden y jerarquía.

    Por defecto devuelve primero los códigos más recientes (descendente).
    ``order="priority"`` mantiene el orden por prioridad y antigüedad.
    ``roots_only`` deja solo las raíces; ``parent_id`` filtra los hijos de un
    ticket; ``leaves_only`` excluye las épicas con tareas. Sin ``detail`` no
    se leen ``body`` ni ``resolution``: el listado no los necesita.
    """
    check_enum(order, ORDER_MODES, "orden")
    filt = filters or TicketFilter(
        status=status,
        statuses=statuses,
        kind=kind,
        priority=priority,
        project=project,
        assignee=assignee,
        query=query,
        include_archived=include_archived,
        tag=tag,
        tags=tags,
        parent_id=parent_id,
        roots_only=roots_only,
        leaves_only=leaves_only,
        include_scratch=include_scratch,
    )
    join, where, params = filt.build(conn)

    if detail:
        columns = "t.*"
    else:
        columns = _lean_projection(conn)
    sql = (
        f"SELECT {columns}, "
        "(SELECT group_concat(tag, ',') FROM tags WHERE ticket_id = t.id) AS tags_csv "
        "FROM tickets t" + join
    )
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += ORDER_BY_PRIORITY if order == "priority" else " ORDER BY t.id DESC"

    offset = max(0, int(offset))
    if limit is None:
        if offset:
            sql += " LIMIT -1 OFFSET ?"
            params.append(offset)
    else:
        sql += " LIMIT ?"
        params.append(max(1, min(int(limit), 500)))
        if offset:
            sql += " OFFSET ?"
            params.append(offset)

    tickets = [row_to_ticket(row) for row in conn.execute(sql, params)]
    progress = subtask_progress(conn, [ticket["code"] for ticket in tickets])
    for ticket in tickets:
        ticket["progress"] = progress.get(
            ticket["code"], {"done": 0, "confirmed": 0, "total": 0}
        )
    return tickets


def count_tickets(
    conn: sqlite3.Connection,
    status: str | None = None,
    statuses: Iterable[str] | None = None,
    kind: str | None = None,
    priority: str | None = None,
    project: str | None = None,
    assignee: str | None = None,
    query: str | None = None,
    include_archived: bool = False,
    tag: str | None = None,
    tags: Iterable[str] | None = None,
    parent_id: Any = UNSET,
    roots_only: bool = False,
    leaves_only: bool = False,
    include_scratch: bool = False,
    filters: TicketFilter | None = None,
) -> int:
    """Cuenta tickets con los mismos filtros que ``list_tickets``."""
    filt = filters or TicketFilter(
        status=status,
        statuses=statuses,
        kind=kind,
        priority=priority,
        project=project,
        assignee=assignee,
        query=query,
        include_archived=include_archived,
        tag=tag,
        tags=tags,
        parent_id=parent_id,
        roots_only=roots_only,
        leaves_only=leaves_only,
        include_scratch=include_scratch,
    )
    join, where, params = filt.build(conn)
    sql = "SELECT COUNT(*) FROM tickets t" + join
    if where:
        sql += " WHERE " + " AND ".join(where)
    row = conn.execute(sql, params).fetchone()
    return int(row[0]) if row else 0


def subtask_progress(
    conn: sqlite3.Connection, codes: Iterable[str]
) -> dict[str, dict[str, int]]:
    """Progreso ``{código: {done, total}}`` de las épicas indicadas."""
    wanted = [code for code in dict.fromkeys(codes) if code]
    if not wanted:
        return {}
    placeholders = ", ".join("?" for _ in wanted)
    rows = conn.execute(
        "SELECT p.code AS code, COUNT(c.id) AS total, "
        "SUM(CASE WHEN c.status IN ("
        + ", ".join("?" for _ in PARENT_DONE_STATUSES)
        + ") THEN 1 ELSE 0 END) AS done, "
        "SUM(CASE WHEN c.status IN ("
        + ", ".join("?" for _ in PARENT_CONFIRMED_STATUSES)
        + ") THEN 1 ELSE 0 END) AS confirmed "
        "FROM tickets p JOIN tickets c ON c.parent_id = p.id "
        f"AND c.archived = 0 WHERE p.code IN ({placeholders}) "
        "GROUP BY p.id",
        (*PARENT_DONE_STATUSES, *PARENT_CONFIRMED_STATUSES, *wanted),
    )
    return {
        row["code"]: {
            "done": int(row["done"] or 0),
            "confirmed": int(row["confirmed"] or 0),
            "total": int(row["total"]),
        }
        for row in rows
    }


def search_codes(
    conn: sqlite3.Connection, query: str, limit: int = 2000
) -> set[str]:
    """Códigos que coinciden con la búsqueda: FTS (título/cuerpo/código) + comentarios.

    FTS5 cubre título, cuerpo y código con prefijos; los comentarios no están
    indexados, así que se buscan con ``LIKE`` exigiendo todos los tokens.
    """
    tokens = search_tokens(query)
    if not tokens:
        return set()

    found: set[str] = set()
    match = fts_match(query)
    if match:
        found.update(
            row[0]
            for row in conn.execute(
                "SELECT t.code FROM tickets t "
                "JOIN tickets_fts ON tickets_fts.rowid = t.id "
                "WHERE tickets_fts MATCH ? AND t.archived = 0 LIMIT ?",
                (match, max(1, min(int(limit), 5000))),
            )
        )

    condition = " AND ".join("c.body LIKE ?" for _ in tokens)
    params = [f"%{token}%" for token in tokens]
    found.update(
        row[0]
        for row in conn.execute(
            "SELECT DISTINCT t.code FROM comments c "
            "JOIN tickets t ON t.id = c.ticket_id "
            f"WHERE {condition} AND t.archived = 0 LIMIT ?",
            (*params, max(1, min(int(limit), 5000))),
        )
    )
    return found


def activity_by_day(
    conn: sqlite3.Connection,
    days: int = 371,
    project: str | None = None,
    agent: str | None = None,
    today: str | None = None,
) -> dict[str, dict[str, int]]:
    """Actividad agregada por día local para el mapa de contribuciones.

    Devuelve ``{YYYY-MM-DD: {"eventos", "creados", "completados"}}`` con una
    sola consulta. La fecha sale de ``substr(ts, 1, 10)``, que respeta el huso
    guardado en el propio timestamp (``date()`` lo convertiría a UTC y movería
    los eventos de noche al día siguiente).

    ``agent`` filtra por el actor real de cada evento; ``project`` por el
    proyecto del ticket. ``today`` permite fijar el día final en las pruebas.
    """
    days = max(1, min(int(days), 3660))
    end = today or datetime.now().date().isoformat()
    start = (date.fromisoformat(end) - timedelta(days=days - 1)).isoformat()
    # El límite superior abarca el día final completo: los timestamps son ISO
    # con prefijo YYYY-MM-DD, así que comparar contra el día siguiente evita
    # aplicar substr() en el WHERE y permite usar idx_events_ts.
    after_end = (date.fromisoformat(end) + timedelta(days=1)).isoformat()

    where = ["e.ts >= ?", "e.ts < ?"]
    params: list[Any] = [start, after_end]
    if project:
        where.append(
            "e.ticket_id IN (SELECT id FROM tickets "
            "WHERE archived = 0 AND project = ?)"
        )
        params.append(project)
    else:
        # Sin filtro de proyecto no hace falta unir tickets: basta excluir
        # los archivados (normalmente pocos) con una subconsulta.
        where.append(
            "e.ticket_id NOT IN (SELECT id FROM tickets WHERE archived = 1)"
        )
    if agent:
        where.append("e.actor = ?")
        params.append(agent)

    rows = conn.execute(
        "SELECT substr(e.ts, 1, 10) AS day, "
        "COUNT(*) AS eventos, "
        "SUM(CASE WHEN e.action = 'crear' THEN 1 ELSE 0 END) AS creados, "
        "SUM(CASE WHEN e.action = 'transicion' AND e.to_status = 'completado' "
        "THEN 1 ELSE 0 END) AS completados "
        "FROM events e "
        f"WHERE {' AND '.join(where)} "
        "GROUP BY day",
        params,
    )
    return {
        row["day"]: {
            "eventos": row["eventos"] or 0,
            "creados": row["creados"] or 0,
            "completados": row["completados"] or 0,
        }
        for row in rows
    }


def recent_events(
    conn: sqlite3.Connection, limit: int = 40
) -> list[dict[str, Any]]:
    """Últimos eventos de todos los tickets, con su código."""
    rows = conn.execute(
        "SELECT e.id, e.ts, e.actor, e.actor_type, e.action, e.from_status, "
        "e.to_status, e.note, t.code AS code "
        "FROM events e JOIN tickets t ON t.id = e.ticket_id "
        "ORDER BY e.id DESC LIMIT ?",
        (max(1, min(int(limit), 200)),),
    )
    return [dict(row) for row in rows]


def next_ticket(
    conn: sqlite3.Connection, project: str | None = None
) -> dict[str, Any] | None:
    """Devuelve el siguiente ticket de mayor prioridad listo para trabajar."""
    for statuses in (["pendiente"], ["nuevo"]):
        rows = list_tickets(
            conn,
            filters=TicketFilter(
                statuses=statuses,
                project=project,
                leaves_only=True,
            ),
            limit=1,
            order="priority",
        )
        if rows:
            return get_ticket(conn, rows[0]["code"])
    return None


def missing_codes(conn: sqlite3.Connection) -> list[dict[str, str]]:
    """Números ``T-`` sin ninguna fila: huecos reales de numeración.

    Un ticket archivado o temporal no es un hueco: su fila existe y
    ``hidden_codes`` lo explica. Solo cuentan los números que nunca
    llegaron a crear ticket.
    """
    next_number = int(db.meta_get(conn, TICKET_COUNTER, "1") or "1")
    expected = max(0, next_number - 1)
    # Camino rápido: si hay tantos códigos planos T- como números emitidos,
    # no falta ninguno. Evita materializar los 20k códigos en Python.
    flat = conn.execute(
        "SELECT COUNT(*) FROM tickets "
        "WHERE code GLOB 'T-*' AND instr(code, '.') = 0"
    ).fetchone()[0]
    if flat == expected:
        return []
    existing = {
        row[0]
        for row in conn.execute(
            "SELECT code FROM tickets WHERE code NOT LIKE '%.%'"
        )
    }
    renames: dict[str, str] = {}
    for row in conn.execute(
        "SELECT note FROM events WHERE action = 'renumerar'"
    ):
        note = row[0] or ""
        if "→" in note:
            old, new = note.split("→", 1)
            renames[old.strip()] = new.strip()

    gaps: list[dict[str, str]] = []
    for number in range(1, next_number):
        code = f"T-{number:04d}"
        if code in existing:
            continue
        new = renames.get(code)
        gaps.append(
            {
                "code": code,
                "reason": f"renumerado a {new}" if new else "sin rastro",
            }
        )
    return gaps


def hidden_codes(conn: sqlite3.Connection) -> list[dict[str, str]]:
    """Códigos ``T-`` cuya fila existe pero está fuera de la tabla visible.

    Es el caso que hace parecer un hueco: el código está consumido, el
    ticket está archivado o es un scratch antiguo con código ``T-``.
    """
    rows = conn.execute(
        "SELECT t.id, t.code, t.title, t.status, t.archived, "
        "EXISTS (SELECT 1 FROM tags sg WHERE sg.ticket_id = t.id "
        "AND sg.tag = ?) AS scratch "
        "FROM tickets t WHERE t.code LIKE 'T-%' AND t.code NOT LIKE '%.%' "
        "AND (t.archived = 1 OR EXISTS (SELECT 1 FROM tags sg "
        "WHERE sg.ticket_id = t.id AND sg.tag = ?)) "
        "ORDER BY t.code",
        (SCRATCH_TAG, SCRATCH_TAG),
    ).fetchall()
    items: list[dict[str, str]] = []
    for row in rows:
        note = conn.execute(
            "SELECT note FROM events WHERE ticket_id = ? "
            "AND action IN ('archivar', 'promover') "
            "ORDER BY ts DESC, id DESC LIMIT 1",
            (row["id"],),
        ).fetchone()
        reason = (note[0] if note else "") or (
            "archivado" if row["archived"] else "ticket temporal"
        )
        items.append(
            {
                "code": row["code"],
                "title": row["title"],
                "status": row["status"],
                "reason": reason,
            }
        )
    return items
