"""Pantalla de memoria de la TUI: propuestas, playbooks y presupuesto.

La lógica de carga, filtros, orden, cabecera y detalle es pura (sin curses)
para poder probarla con una base de datos temporal. ``run`` dibuja la pantalla
y delega las mutaciones en ``ticketlib.memory``; aprobar y rechazar son
acciones explícitas del usuario y nada se borra: archivar conserva el ledger.
"""

from __future__ import annotations

import curses
import sqlite3
from typing import Any

from ticketlib import db, workspace
from ticketlib.domain import TicketError, ValidationError
from ticketlib.memory import (
    PLAYBOOK_KIND,
    approve_memory,
    archive_memory,
    export_playbook,
    get_memory,
    get_playbook,
    list_memories,
    load_memory_config,
    pin_memory,
    reject_memory,
)
from ticketlib.memory import budget as budget_lib
from ticketlib.memory import core as memory_core
from ticketlib.memory.stats import stats as memory_stats

from .detail import wrap
from .view import Palette, confirm_dialog, modal, prompt, put

USER_ACTOR = "usuario"
FILTERS = ("propuestas", "activas", "stale", "playbooks", "todas")
FILTER_LABELS = {
    "propuestas": "propuestas",
    "activas": "activas",
    "stale": "stale",
    "playbooks": "playbooks",
    "todas": "todas",
}
FILTER_STATUSES = {
    "propuestas": ("proposed",),
    "activas": ("active",),
    "stale": ("stale",),
}
LIVE_STATUSES = ("proposed", "active", "stale")
STATUS_ORDER = {
    "proposed": 0,
    "active": 1,
    "stale": 2,
    "archived": 3,
    "superseded": 4,
}
KIND_ORDER = {
    "convention": 0,
    "pitfall": 1,
    "decision": 2,
    "workflow": 3,
    "fact": 4,
    "preference": 5,
    "reference": 6,
    PLAYBOOK_KIND: 7,
}
STATUS_LABELS = {
    "proposed": "propuesta",
    "active": "activa",
    "stale": "stale",
    "archived": "archivada",
    "superseded": "superseded",
}
MAX_ROWS = 1000
MEMORY_HELP = (
    "Atajos de la pantalla Memoria",
    "",
    "  j/k ↑↓           Mover por la lista (o el detalle con foco)",
    "  PgUp/PgDn        Página arriba / abajo",
    "  g/G              Ir al inicio / final",
    "  tab              Cambiar el foco entre lista y detalle",
    "  h/l  ←/→         Filtro anterior / siguiente",
    "  a                Aprobar la propuesta seleccionada",
    "  x                Rechazar con motivo (archiva, no borra)",
    "  p                Fijar o desfijar en el brief",
    "  r                Archivar con confirmación",
    "  o                Exportar el playbook como skill",
    "  ?                Esta ayuda",
    "  q / Esc / Y      Cerrar la pantalla Memoria",
    "",
    "  Aprobar y rechazar son acciones del usuario; la TUI nunca borra.",
    "",
    "  Pulsa cualquier tecla para cerrar",
)


def load_memories(
    conn: sqlite3.Connection,
    config: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Carga memorias, playbooks y la foto de presupuesto/estado."""
    cfg = memory_core.config(config)
    if not cfg["enabled"]:
        return {
            "enabled": False,
            "config": cfg,
            "memories": [],
            "playbooks": [],
            "stats": None,
        }
    memories = list_memories(conn, include_inactive=True, limit=MAX_ROWS)
    playbooks = [
        memory for memory in memories if memory.get("kind") == PLAYBOOK_KIND
    ]
    return {
        "enabled": True,
        "config": cfg,
        "memories": memories,
        "playbooks": playbooks,
        "stats": memory_stats(conn, cfg),
    }


def filter_memories(
    memories: list[dict[str, Any]],
    mode: str = "todas",
) -> list[dict[str, Any]]:
    """Filtra por propuestas, activas, stale, playbooks o todas."""
    if mode == "todas":
        return list(memories)
    if mode == "playbooks":
        return [
            memory
            for memory in memories
            if memory.get("kind") == PLAYBOOK_KIND
            and memory.get("status") in LIVE_STATUSES
        ]
    statuses = FILTER_STATUSES.get(mode)
    if statuses is None:
        raise ValueError(f"Filtro de memoria desconocido: {mode!r}")
    return [
        memory for memory in memories if memory.get("status") in statuses
    ]


def sort_memories(memories: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Ordena por estado, tipo y código para una revisión estable."""
    return sorted(
        memories,
        key=lambda memory: (
            STATUS_ORDER.get(str(memory.get("status")), 99),
            KIND_ORDER.get(str(memory.get("kind")), 99),
            str(memory.get("code") or ""),
        ),
    )


def budget_lines(data: dict[str, Any]) -> list[str]:
    """Líneas de presupuesto project/user y frescura del curado."""
    cfg = data.get("config") or {}
    stats = data.get("stats") or {}
    budget = stats.get("budget") or {}
    lines = []
    for scope, label in (("project", "proyecto"), ("user", "usuario")):
        item = budget.get(scope) or {}
        lines.append(
            f"Presupuesto {label}: {item.get('used', 0)}/"
            f"{item.get('limit', 0)} ({item.get('entries', 0)} entradas)"
        )
    lines.append(
        "frescura stale "
        f"{cfg.get('stale_after_days', 0)}d · archivo "
        f"{cfg.get('archive_after_days', 0)}d"
    )
    return lines


def header_lines(data: dict[str, Any]) -> list[str]:
    """Cabecera con conteos por estado, presupuesto, playbooks y frescura."""
    cfg = data.get("config") or {}
    if not data.get("enabled"):
        return [
            "Memoria deshabilitada en la configuración.",
            "Activa memory.enabled para revisar propuestas y playbooks.",
        ]
    stats = data.get("stats") or {}
    by_status = stats.get("by_status") or {}
    counts = " · ".join(
        f"{label} {by_status.get(status, 0)}"
        for status, label in (
            ("active", "activas"),
            ("proposed", "propuestas"),
            ("stale", "stale"),
            ("archived", "archivadas"),
            ("superseded", "superseded"),
        )
    )
    playbooks = len(data.get("playbooks") or [])
    first = f"Memoria · {counts} · playbooks {playbooks} · {stats.get('total', 0)} totales"
    second = " · ".join(budget_lines(data)) + (
        f" · límite entrada {cfg.get('max_entry_chars', 0)}c"
    )
    return [first, second]


def row_text(memory: dict[str, Any], width: int = 0) -> str:
    """Una fila de la lista: código, estado, tipo y título."""
    code = str(memory.get("code") or "")
    status = str(memory.get("status") or "")
    kind = str(memory.get("kind") or "")
    title = str(memory.get("title") or "")
    text = f"{code:<7} {status:<10} {kind:<11} {title}"
    if width > 1 and len(text) > width:
        text = text[: width - 1] + "…"
    return text


def _detail_memory(
    conn: sqlite3.Connection, memory: dict[str, Any]
) -> dict[str, Any]:
    try:
        return get_memory(conn, memory["code"])
    except (sqlite3.Error, ValidationError):
        return memory


def detail_lines(
    conn: sqlite3.Connection,
    memory: dict[str, Any] | None,
    config: dict[str, Any] | None = None,
    width: int = 60,
) -> list[str]:
    """Detalle legible: estado, contenido, evidencia, uso, presupuesto y files."""
    if memory is None:
        return ["Sin entradas para el filtro actual."]
    cfg = memory_core.config(config)
    data = _detail_memory(conn, memory)
    code = str(data.get("code") or "")
    status = str(data.get("status") or "")
    kind = str(data.get("kind") or "")
    scope = str(data.get("scope") or "")
    title = str(data.get("title") or "")
    lines = [f"{code} · {STATUS_LABELS.get(status, status)} · {kind} · {scope}", f"Título: {title}"]
    key = str(data.get("key") or "")
    if key:
        lines.append(f"Clave: {key}")
    description = str(data.get("description") or "")
    if description:
        lines.append(f"Descripción: {description}")
    pinned = "sí" if data.get("pinned") else "no"
    confidence = float(data.get("confidence") or 0.0)
    use_count = int(data.get("use_count") or 0)
    helpful = int(data.get("helpful_count") or 0)
    unhelpful = int(data.get("unhelpful_count") or 0)
    lines.append(
        f"Confianza: {confidence:.2f} · pin: {pinned} · uso: {use_count} "
        f"(+{helpful}/-{unhelpful})"
    )
    last_used = str(data.get("last_used_at") or "")
    if last_used:
        lines.append(f"Último uso: {last_used}")
    ticket_kinds = data.get("ticket_kinds") or []
    if ticket_kinds:
        lines.append("Tipos de ticket: " + ", ".join(ticket_kinds))
    paths = data.get("paths") or []
    if paths:
        lines.append("Rutas: " + ", ".join(paths))
    entry_scope = scope or "project"
    limit = memory_core.limit_for(cfg, entry_scope)
    summary = budget_lib.summary(
        memory_core.scope_entries(conn, entry_scope), limit, entry_scope
    )
    lines.append(
        f"Presupuesto {entry_scope}: {summary['used']}/{summary['limit']} "
        f"({summary['entries']} entradas)"
    )
    evidence = data.get("evidence") or []
    if evidence:
        lines.append("Evidencia:")
        lines.extend(
            f"  - {item.get('kind')}:{item.get('ref')}" for item in evidence
        )
    files = data.get("files") or []
    if files:
        lines.append("Ficheros:")
        lines.extend(f"  - {item.get('path')}" for item in files)
    slug = str(data.get("key") or "").removeprefix("playbook:")
    if kind == PLAYBOOK_KIND and slug:
        lines.append(f"Playbook: {slug} (o exporta con o)")
    content = str(data.get("content") or "").strip()
    if content:
        lines.append("Contenido:")
        lines.extend("  " + chunk for chunk in wrap(content, max(12, width - 2)))
    return lines


def _require_user(actor: str) -> None:
    if actor != USER_ACTOR:
        raise PermissionError(
            "Aprobar, rechazar, fijar y archivar memorias es una acción del usuario"
        )


def approve(
    conn: sqlite3.Connection,
    code: str,
    *,
    actor: str = USER_ACTOR,
    session_id: str | None = None,
    reason: str | None = None,
    config: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Aprueba una propuesta (solo el usuario)."""
    _require_user(actor)
    return approve_memory(
        conn,
        code,
        actor=actor,
        session_id=session_id,
        reason=reason,
        config=config,
    )


def reject(
    conn: sqlite3.Connection,
    code: str,
    reason: str,
    *,
    actor: str = USER_ACTOR,
    session_id: str | None = None,
    config: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Rechaza una propuesta con motivo; archiva, nunca borra."""
    _require_user(actor)
    text = str(reason or "").strip()
    if not text:
        raise ValidationError("El rechazo de una propuesta exige un motivo")
    return reject_memory(
        conn,
        code,
        actor=actor,
        session_id=session_id,
        reason=text,
        config=config,
    )


def toggle_pin(
    conn: sqlite3.Connection,
    code: str,
    *,
    actor: str = USER_ACTOR,
    session_id: str | None = None,
    reason: str | None = None,
    config: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Fija o desfija una memoria en el brief."""
    _require_user(actor)
    current = get_memory(conn, code)
    return pin_memory(
        conn,
        code,
        pinned=not bool(current.get("pinned")),
        actor=actor,
        session_id=session_id,
        reason=reason,
        config=config,
    )


def archive(
    conn: sqlite3.Connection,
    code: str,
    *,
    actor: str = USER_ACTOR,
    session_id: str | None = None,
    reason: str | None = None,
    config: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Archiva con histórico; no elimina la memoria."""
    _require_user(actor)
    return archive_memory(
        conn,
        code,
        actor=actor,
        session_id=session_id,
        reason=reason,
        config=config,
    )


def export(
    conn: sqlite3.Connection,
    code: str,
    *,
    root: str | None = None,
    config: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Exporta un playbook a ``.taiki/generated/skills/<slug>``."""
    memory = get_playbook(conn, code)
    target = root or str(workspace.project_root())
    return export_playbook(target, memory, conn=conn, config=config)


def close_keys() -> tuple[int, ...]:  # pragma: no cover - UI
    """Teclas que cierran la pantalla."""
    return (ord("q"), 27, ord("Y"), ord("y"))


def _move(  # pragma: no cover - UI
    key: int,
    cursor: int,
    total: int,
    page: int,
) -> int:
    if key in (curses.KEY_UP, ord("k")):
        return max(0, cursor - 1)
    if key in (curses.KEY_DOWN, ord("j")):
        return min(max(0, total - 1), cursor + 1)
    if key == curses.KEY_PPAGE:
        return max(0, cursor - page)
    if key == curses.KEY_NPAGE:
        return min(max(0, total - 1), cursor + page)
    if key in (curses.KEY_HOME, ord("g")):
        return 0
    if key in (curses.KEY_END, ord("G")):
        return max(0, total - 1)
    return cursor


def _paint(  # pragma: no cover - UI curses
    stdscr,
    conn: sqlite3.Connection,
    data: dict[str, Any],
    memories: list[dict[str, Any]],
    mode: str,
    cursor: int,
    scroll: int,
    focus: str,
    detail_scroll: int,
    message: tuple[str, str] | None,
    palette: Palette,
) -> None:
    height, width = stdscr.getmaxyx()
    stdscr.erase()
    headers = header_lines(data)
    title = f" Memoria "
    put(
        stdscr,
        0,
        0,
        (title + " · " + headers[0])[: max(0, width - 1)],
        curses.A_BOLD | curses.A_REVERSE,
    )
    if len(headers) > 1:
        put(stdscr, 1, 0, headers[1][: max(0, width - 1)], curses.A_DIM)
    detail_visible = width >= 96
    detail_width = min(58, max(44, width // 2)) if detail_visible else 0
    list_width = width - detail_width - (1 if detail_visible else 0)
    put(
        stdscr,
        2,
        0,
        (
            f"Filtro: {FILTER_LABELS.get(mode, mode)} · "
            f"{len(memories)} entrada(s) · foco: {focus}"
        )[: max(0, width - 1)],
    )
    columns = f"{'CÓDIGO':<7} {'ESTADO':<10} {'TIPO':<11} TÍTULO"
    put(stdscr, 3, 0, columns[: max(0, list_width - 1)], curses.A_BOLD)
    list_height = max(1, height - 6)
    visible = memories[scroll : scroll + list_height]
    for offset, memory in enumerate(visible):
        selected = focus == "list" and scroll + offset == cursor
        attr = curses.A_REVERSE | curses.A_BOLD if selected else 0
        put(
            stdscr,
            4 + offset,
            0,
            row_text(memory, list_width - 1).ljust(max(0, list_width - 1)),
            attr,
        )

    detail_x = list_width
    if detail_visible:
        for row in range(3, max(4, height - 2)):
            put(stdscr, row, detail_x - 1, "│", curses.A_DIM)
        put(stdscr, 3, detail_x + 1, "DETALLE", curses.A_BOLD)
        current = memories[cursor] if memories else None
        lines = detail_lines(conn, current, data.get("config"), detail_width - 2)
        if focus == "detail":
            start = max(0, min(detail_scroll, max(0, len(lines) - 1)))
        else:
            start = 0
        for offset, line in enumerate(lines[start : start + list_height]):
            put(
                stdscr,
                4 + offset,
                detail_x + 1,
                line[: max(0, detail_width - 2)],
            )

    if message is not None:
        text, level = message
        put(stdscr, height - 2, 0, text[: max(0, width - 1)], palette.toast(level))
    if width >= 110:
        help_text = (
            " j/k mover · tab foco · h/l filtro · a aprobar · x rechazar · "
            "p pin · r archivar · o exportar · ? ayuda · q salir"
        )
    else:
        help_text = " j/k · h/l filtro · a aprobar · x rechazar · ? ayuda · q salir"
    put(
        stdscr,
        height - 1,
        0,
        help_text[: max(0, width - 1)].ljust(max(0, width - 1)),
        curses.A_REVERSE,
    )
    stdscr.refresh()


def _loop(  # pragma: no cover - UI curses
    stdscr,
    conn: sqlite3.Connection,
    config: dict[str, Any] | None,
) -> None:
    curses.curs_set(0)
    curses.set_escdelay(25)
    stdscr.keypad(True)
    stdscr.timeout(-1)
    palette = Palette()
    data = load_memories(conn, config)
    mode = FILTERS[0]
    cursor = 0
    scroll = 0
    detail_scroll = 0
    focus = "list"
    message: tuple[str, str] | None = None

    while True:
        memories = sort_memories(filter_memories(data["memories"], mode))
        if memories:
            cursor = max(0, min(cursor, len(memories) - 1))
        else:
            cursor = 0
        height, _ = stdscr.getmaxyx()
        list_height = max(1, height - 6)
        scroll = max(0, min(scroll, max(0, len(memories) - list_height)))
        if cursor < scroll:
            scroll = cursor
        elif cursor >= scroll + list_height:
            scroll = cursor - list_height + 1
        _paint(
            stdscr,
            conn,
            data,
            memories,
            mode,
            cursor,
            scroll,
            focus,
            detail_scroll,
            message,
            palette,
        )
        key = stdscr.getch()
        if key == -1:
            continue
        if key in close_keys():
            return
        if key == ord("?"):
            modal(stdscr, list(MEMORY_HELP), title="Memoria")
            continue
        if key == ord("\t"):
            focus = "detail" if focus == "list" else "list"
            detail_scroll = 0
            continue
        if key in (ord("h"), curses.KEY_LEFT):
            index = (FILTERS.index(mode) - 1) % len(FILTERS)
            mode = FILTERS[index]
            cursor = scroll = detail_scroll = 0
            message = (f"Filtro: {FILTER_LABELS[mode]}.", "info")
            continue
        if key in (ord("l"), curses.KEY_RIGHT):
            index = (FILTERS.index(mode) + 1) % len(FILTERS)
            mode = FILTERS[index]
            cursor = scroll = detail_scroll = 0
            message = (f"Filtro: {FILTER_LABELS[mode]}.", "info")
            continue
        if key in (
            curses.KEY_UP,
            curses.KEY_DOWN,
            curses.KEY_PPAGE,
            curses.KEY_NPAGE,
            curses.KEY_HOME,
            curses.KEY_END,
            ord("j"),
            ord("k"),
            ord("g"),
            ord("G"),
        ):
            if focus == "detail":
                detail_scroll = _move(key, detail_scroll, len(memories), list_height)
            else:
                cursor = _move(key, cursor, len(memories), list_height)
            continue
        if key not in (ord("a"), ord("x"), ord("p"), ord("r"), ord("o")):
            continue

        current = memories[cursor] if memories else None
        if current is None:
            message = ("No hay memorias para esta acción.", "info")
            continue
        code = str(current.get("code") or "")
        if not data.get("enabled"):
            message = ("La memoria está deshabilitada.", "error")
            continue
        try:
            if key == ord("a"):
                approve(conn, code, config=data.get("config"))
                message = (f"{code} aprobada.", "ok")
            elif key == ord("x"):
                reason = prompt(stdscr, f"Motivo del rechazo de {code}: ")
                if not reason:
                    message = ("Rechazo cancelado: falta el motivo.", "info")
                else:
                    reject(conn, code, reason, config=data.get("config"))
                    message = (f"{code} rechazada y archivada.", "ok")
            elif key == ord("p"):
                result = toggle_pin(conn, code, config=data.get("config"))
                action = "fijada" if result.get("pinned") else "desfijada"
                message = (f"{code} {action}.", "ok")
            elif key == ord("r"):
                if confirm_dialog(
                    stdscr,
                    "Archivar memoria",
                    [f"{code} · {current.get('title', '')}", "", "Se conserva el histórico."],
                ):
                    archive(conn, code, reason="archivada desde la TUI", config=data.get("config"))
                    message = (f"{code} archivada.", "ok")
                else:
                    message = ("Archivado cancelado.", "info")
            else:
                if current.get("kind") != PLAYBOOK_KIND:
                    message = (f"{code} no es un playbook.", "info")
                else:
                    result = export(conn, code, config=data.get("config"))
                    message = (f"Playbook exportado: {result['path']}", "ok")
        except (PermissionError, TicketError, sqlite3.Error) as exc:
            message = (f"Error: {exc}", "error")
        data = load_memories(conn, config)


def run(  # pragma: no cover - UI curses
    stdscr,
    conn: sqlite3.Connection | None = None,
    config: dict[str, Any] | None = None,
) -> None:
    """Abre la pantalla Memoria; cierra con q, Esc o Y."""
    own = conn is None
    if conn is None:
        conn = db.connect()
    try:
        _loop(stdscr, conn, config)
    finally:
        if own:
            conn.close()


__all__ = [
    "FILTERS",
    "FILTER_LABELS",
    "MAX_ROWS",
    "MEMORY_HELP",
    "STATUS_LABELS",
    "USER_ACTOR",
    "approve",
    "archive",
    "budget_lines",
    "detail_lines",
    "export",
    "filter_memories",
    "header_lines",
    "load_memories",
    "reject",
    "row_text",
    "run",
    "sort_memories",
    "toggle_pin",
]
