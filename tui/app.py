"""Bucle principal de la TUI: entrada, ratón y acciones."""

from __future__ import annotations

import curses
import sqlite3
import time
from typing import Any

from ticketlib import db, domain, repo, workspace
from ticketlib.domain import OPEN_STATUSES, PRIORITIES, STATUSES
from ticketlib.domain import KINDS

from . import actions_ui, heatmap
from .data import DataSource
from .state import (
    DEFAULT_SCOPE,
    ORDER_MODES,
    PANE_MESSAGES,
    SCOPE_LABELS,
    SCOPES,
    TuiState,
    layout_width,
)
from .view import (
    Palette,
    choose,
    draw_columns,
    draw_header,
    draw_panel,
    draw_rows,
    draw_statusbar,
    draw_too_small,
    geometry,
    help_modal,
    menu,
    prompt,
    put,
    set_redraw,
)

FILTER_ITEMS = (
    "Ámbito (atención / todos / completados / abiertos / cerrados)",
    "Estado exacto",
    "Prioridad",
    "Tipo",
    "Agente",
    "Proyecto",
    "Etiqueta",
    "Orden (código / prioridad)",
    "Limpiar filtros",
)
MAX_LIMIT = 2000
PAGE = 200


def _refresh(state: TuiState, source: DataSource, force: bool = False) -> bool:
    """Relee la base de datos y devuelve True si cambió algo visible."""
    current = state.current()
    source.configure(limit=state.limit, order=state.filters.order)
    known = set(state.fresh)
    try:
        conn = db.connect()
        try:
            snapshot = source.poll(
                conn,
                force=force,
                query=state.filters.query,
                project=state.filters.project,
                agent=state.filters.agent,
            )
        finally:
            conn.close()
    except sqlite3.Error as exc:
        state.notify(f"Base de datos no disponible: {exc}", "error", ttl=8)
        return False
    if snapshot is None:
        return False

    before = state.fingerprint()
    state.load(
        snapshot.tickets,
        snapshot.session_map,
        keep_code=current["code"] if current else None,
        truncated=snapshot.truncated,
        stats=snapshot.stats,
        activity=snapshot.activity,
        search_codes=snapshot.search_codes,
        session_dirs=snapshot.session_dirs,
        mcp_stale=snapshot.mcp_stale,
        mcp_orphans=snapshot.mcp_orphans,
        heatmap=snapshot.heatmap,
    )
    changed = state.fingerprint() != before
    new_codes = [code for code in state.fresh if code not in known]
    if changed and new_codes and not force:
        state.notify(f"{len(new_codes)} ticket(s) con novedades.", "info", ttl=4)
    return changed


def _load_more(state: TuiState, source: DataSource) -> bool:
    if not state.truncated or state.limit >= MAX_LIMIT:
        return False
    state.limit = min(MAX_LIMIT, state.limit + PAGE)
    source.configure(limit=state.limit)
    _refresh(state, source, force=True)
    state.notify(f"Cargando hasta {state.limit} tickets.", "info", ttl=3)
    return True


def _ensure_detail(state: TuiState) -> None:
    """Carga el ticket completo del cursor (comentarios, historial, enlaces)."""
    ticket = state.current()
    if ticket is None:
        state.detail = None
        state.detail_key = None
        return
    latest = state.activity[0].get("id") if state.activity else None
    key = (ticket["code"], ticket.get("updated_at"), latest)
    if key == state.detail_key:
        return
    try:
        conn = db.connect()
        try:
            state.detail = repo.get_ticket(conn, ticket["code"])
        finally:
            conn.close()
    except (sqlite3.Error, domain.TicketError):
        state.detail = ticket
    state.detail_key = key


def confirm_codes(codes: list[str]) -> dict[str, Any]:
    conn = db.connect()
    try:
        return repo.confirm_tickets(conn, codes)
    finally:
        conn.close()


def _handle_mouse(state: TuiState, layout) -> None:
    try:
        _, mx, my, _, bstate = curses.getmouse()
    except curses.error:
        return

    if bstate & (curses.BUTTON4_PRESSED | curses.BUTTON4_CLICKED):
        if state.focus == "detail":
            if state.pane == "heatmap":
                state.heatmap_move(-7)
            else:
                state.scroll_panel(-3, layout.list_height, 10_000)
        else:
            state.move(-3)
        return
    if bstate & (curses.BUTTON5_PRESSED | curses.BUTTON5_CLICKED):
        if state.focus == "detail":
            if state.pane == "heatmap":
                state.heatmap_move(7)
            else:
                state.scroll_panel(3, layout.list_height, 10_000)
        else:
            state.move(3)
        return
    if not bstate & curses.BUTTON1_PRESSED:
        return

    if layout.detail_visible and mx >= layout.detail_x:
        state.focus = "detail"
        if my == layout.columns_y:
            state.toggle_pane()
            return
        if state.pane == "heatmap":
            view = heatmap.render_map(
                state.heatmap,
                metric=state.heatmap_metric,
                width=layout.detail_width - 1,
                selected=state.heatmap_day(),
            )
            row = my - (layout.list_top + heatmap.GRID_TOP)
            column = (mx - layout.detail_x - heatmap.GUTTER) // heatmap.WEEK_WIDTH
            cell = heatmap.day_at(view, row, column)
            if cell is not None:
                state.heatmap_cursor = cell.day.isoformat()
        return

    if my < layout.list_top or my >= layout.list_top + layout.list_height:
        return
    index = state.scroll + (my - layout.list_top)
    rows = state.rows()
    if index >= len(rows):
        return

    state.focus = "list"
    state.cursor = index
    state.detail_scroll = 0
    ticket, _, epic = rows[index]
    column = layout.column_at(mx)
    if column == "toggle":
        if not state.toggle():
            state.notify("Solo se pueden marcar tickets 'completado'.", "info")
    elif column in ("title", "code") and epic:
        state.toggle_expanded(ticket["code"])
    elif column == "agent":
        actions_ui.open_session(state, ticket)


def _filter_menu(win, state: TuiState, refresh) -> None:
    picked = menu(win, "Filtros", list(FILTER_ITEMS))
    if picked is None:
        return
    name = (
        "scope",
        "status",
        "priority",
        "kind",
        "agent",
        "project",
        "tag",
        "order",
        "clear",
    )[picked]

    if name == "clear":
        state.clear_filters()
        state.notify("Filtros limpiados.", "info")
        refresh(True)
        return
    if name == "order":
        chosen = menu(
            win,
            "Orden",
            list(ORDER_MODES),
            ORDER_MODES.index(state.filters.order),
        )
        if chosen is None:
            return
        state.set_filter("order", ORDER_MODES[chosen])
        state.notify(f"Orden: {state.filters.order}.", "info")
        refresh(True)
        return

    if name == "scope":
        options = list(SCOPES)
        current = state.filters.scope
    elif name == "status":
        options = list(STATUSES)
        current = state.filters.status
    elif name == "priority":
        options = list(PRIORITIES)
        current = state.filters.priority
    elif name == "kind":
        options = list(KINDS)
        current = state.filters.kind
    elif name == "agent":
        options = state.agent_options()
        current = state.filters.agent
    elif name == "project":
        options = state.project_options()
        current = state.filters.project
    else:
        options = state.tag_options()
        current = state.filters.tag

    accepted, value = choose(win, f"Filtro: {name}", options, current)
    if not accepted:
        return
    if name == "scope":
        state.set_scope(value or DEFAULT_SCOPE)
        value = SCOPE_LABELS.get(state.filters.scope, state.filters.scope)
    else:
        state.set_filter(name, value)
    state.notify(
        f"Filtro {name}: {value or 'todos'}.", "info"
    )


def _initial_state(
    statuses: list[str] | None,
    project: str | None,
    query: str | None,
    limit: int,
    order: str = "code",
) -> TuiState:
    state = TuiState(limit=limit)
    state.filters.order = order if order in ORDER_MODES else "code"
    if statuses:
        if set(statuses) == set(OPEN_STATUSES):
            state.set_scope("abiertos")
        elif statuses == ["completado"]:
            state.set_scope("completados")
        else:
            # Un estado explícito (p. ej. `--status confirmado`) debe verse
            # aunque el ámbito por defecto oculte los cerrados.
            state.set_scope("todos")
            if len(statuses) == 1:
                state.set_filter("status", statuses[0])
    if project:
        state.set_filter("project", project)
    if query:
        state.set_query(query)
    return state


def main_loop(
    stdscr,
    statuses: list[str] | None,
    project: str | None,
    query: str | None,
    limit: int,
    refresh: float = 2.0,
    order: str = "code",
) -> None:
    curses.curs_set(0)
    curses.set_escdelay(25)
    stdscr.keypad(True)
    try:
        curses.mousemask(curses.ALL_MOUSE_EVENTS)
        curses.mouseinterval(0)
    except curses.error:
        pass

    palette = Palette()
    state = _initial_state(statuses, project, query, limit, order)
    state.workspace_label = workspace.label()
    source = DataSource(limit=state.limit, order=state.filters.order)
    _refresh(state, source, force=True)

    minimum = layout_width(False)
    interval = max(0.0, float(refresh))
    timeout_ms = int(interval * 1000) if interval > 0 else -1
    last_check = time.monotonic()
    dirty = True

    def refresh(force: bool = False) -> None:
        nonlocal dirty
        if _refresh(state, source, force=force):
            dirty = True

    def paint():
        """Dibuja el fotograma completo y devuelve su geometría."""
        height, width = stdscr.getmaxyx()
        layout = geometry(width, height, state)
        _ensure_detail(state)
        stdscr.erase()
        draw_header(stdscr, layout, state, interval, palette)
        draw_columns(stdscr, layout, state)
        put(
            stdscr,
            3,
            0,
            "─" * max(0, layout.detail_x - 1),
            curses.A_DIM,
        )
        draw_rows(stdscr, layout, state, palette)
        draw_panel(stdscr, layout, state, palette)
        draw_statusbar(stdscr, layout, state, interval, palette)
        stdscr.refresh()
        return layout

    set_redraw(paint)

    while True:
        stdscr.timeout(timeout_ms)
        height, width = stdscr.getmaxyx()
        if height < 8 or width < minimum:
            draw_too_small(stdscr, width, height, minimum)
            key = stdscr.getch()
            if key in (ord("q"), 27):
                return
            continue

        layout = geometry(width, height, state)
        if interval > 0 and state.auto_refresh:
            now = time.monotonic()
            if now - last_check >= interval:
                last_check = now
                refresh()
        elif interval > 0:
            last_check = time.monotonic()

        state.prune_toasts()
        if dirty:
            layout = paint()
            dirty = False

        key = stdscr.getch()
        if key == -1:
            continue
        dirty = True

        if key in (ord("q"), ord("Q")):
            return

        if state.focus == "detail" and state.pane == "heatmap" and key in (
            curses.KEY_UP,
            curses.KEY_DOWN,
            curses.KEY_LEFT,
            curses.KEY_RIGHT,
            curses.KEY_PPAGE,
            curses.KEY_NPAGE,
            curses.KEY_HOME,
            curses.KEY_END,
            ord("j"),
            ord("k"),
            ord("h"),
            ord("l"),
            ord("g"),
            ord("G"),
        ):
            if key in (curses.KEY_UP, ord("k")):
                state.heatmap_move(-1)
            elif key in (curses.KEY_DOWN, ord("j")):
                state.heatmap_move(1)
            elif key in (curses.KEY_LEFT, ord("h")):
                state.heatmap_move(-7)
            elif key in (curses.KEY_RIGHT, ord("l")):
                state.heatmap_move(7)
            elif key == curses.KEY_PPAGE:
                state.heatmap_move(-28)
            elif key == curses.KEY_NPAGE:
                state.heatmap_move(28)
            elif key in (curses.KEY_HOME, ord("g")):
                state.heatmap_cursor = state.heatmap_first_day().isoformat()
            else:
                state.heatmap_today()
            continue

        if state.pane == "heatmap" and key == ord("t"):
            metric = state.heatmap_cycle_metric()
            state.notify(f"Métrica del mapa: {metric}.", "info", ttl=3)
            continue

        if state.focus == "detail" and key in (
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
            if key in (curses.KEY_UP, ord("k")):
                state.scroll_panel(-1, layout.list_height, 10_000)
            elif key in (curses.KEY_DOWN, ord("j")):
                state.scroll_panel(1, layout.list_height, 10_000)
            elif key == curses.KEY_PPAGE:
                state.scroll_panel(-layout.list_height, layout.list_height, 10_000)
            elif key == curses.KEY_NPAGE:
                state.scroll_panel(layout.list_height, layout.list_height, 10_000)
            elif key in (curses.KEY_HOME, ord("g")):
                state.detail_scroll = 0
            else:
                state.detail_scroll = 10_000
            continue

        if key == ord("\t"):
            state.toggle_focus()
        elif key == ord("d"):
            visible = state.toggle_detail()
            state.notify(
                "Panel de detalle visible." if visible else "Panel oculto.",
                "info",
                ttl=3,
            )
        elif key == ord("v"):
            state.pane = state.toggle_pane()
            state.focus = "detail"
            state.detail_scroll = 0
            state.notify(
                PANE_MESSAGES.get(state.pane, "Panel."), "info", ttl=3
            )
        elif key in (curses.KEY_UP, ord("k")):
            state.move(-1)
        elif key in (curses.KEY_DOWN, ord("j")):
            state.move(1)
            if state.needs_more():
                _load_more(state, source)
        elif key == curses.KEY_PPAGE:
            state.move(-layout.list_height)
        elif key == curses.KEY_NPAGE:
            state.move(layout.list_height)
            if state.needs_more():
                _load_more(state, source)
        elif key in (curses.KEY_HOME, ord("g")):
            state.top()
        elif key in (curses.KEY_END, ord("G")):
            state.bottom()
            if state.needs_more():
                _load_more(state, source)
        elif key in (10, 13):
            ticket = state.current()
            if ticket is not None and state.is_epic(ticket):
                state.toggle_expanded(ticket["code"])
            elif not state.toggle():
                state.notify("Solo se pueden marcar tickets 'completado'.", "info")
        elif key == ord(" "):
            if not state.toggle():
                state.notify("Solo se pueden marcar tickets 'completado'.", "info")
        elif key in (curses.KEY_RIGHT, ord("l")):
            ticket = state.current()
            if ticket is not None and state.is_epic(ticket):
                state.expanded.add(ticket["code"])
        elif key in (curses.KEY_LEFT, ord("h")):
            ticket = state.current()
            if (
                ticket is not None
                and state.is_epic(ticket)
                and state.is_expanded(ticket["code"])
            ):
                state.expanded.discard(ticket["code"])
            else:
                state.focus_parent()
        elif key == ord("z"):
            state.collapse_all()
            state.notify("Épicas plegadas.", "info", ttl=3)
        elif key == ord("Z"):
            count = state.expand_all()
            state.notify(f"{count} épica(s) desplegadas.", "info", ttl=3)
        elif key == ord("a"):
            state.notify(f"Marcados {state.select_all()} completados.", "info")
        elif key == ord("A"):
            state.clear_selection()
            state.notify("Selección vacía.", "info")
        elif key == ord("c"):
            if actions_ui.confirm_selection(stdscr, state):
                try:
                    report = confirm_codes(state.selected_codes())
                except (domain.TicketError, sqlite3.Error) as exc:
                    state.notify(f"Error: {exc}", "error")
                else:
                    text, level = actions_ui.report_text(report)
                    state.notify(text, level, ttl=8)
                    state.clear_selection()
                    refresh(True)
        elif key == ord("M"):
            scope = state.toggle_closed_scope()
            state.notify(
                f"Ámbito: {SCOPE_LABELS.get(scope, scope)}.", "info", ttl=3
            )
        elif key == ord("m"):
            actions_ui.run_action(stdscr, state, state.current(), refresh)
        elif key == ord("o"):
            actions_ui.open_session(state, state.current())
        elif key == ord("r"):
            refresh(True)
            state.notify("Recargado.", "ok", ttl=3)
        elif key == ord("R"):
            state.auto_refresh = not state.auto_refresh
            state.notify(
                f"Auto-refresh activado ({interval:g}s)."
                if state.auto_refresh
                else "Auto-refresh en pausa.",
                "info",
                ttl=3,
            )
        elif key == ord("s"):
            scope = state.cycle_scope()
            state.notify(
                f"Ámbito: {SCOPE_LABELS.get(scope, scope)}.", "info", ttl=3
            )
        elif key == ord("x"):
            state.clear_filters()
            refresh(True)
            state.notify("Filtros limpiados.", "info", ttl=3)
        elif key == ord("f"):
            _filter_menu(stdscr, state, refresh)
        elif key == ord("O"):
            order = state.cycle_order()
            refresh(True)
            state.notify(f"Orden: {order}.", "info", ttl=3)
        elif key == ord("n"):
            if not _load_more(state, source):
                state.notify("No hay más tickets que cargar.", "info", ttl=3)
        elif key == ord("/"):
            state.set_query(prompt(stdscr, "Buscar: ", state.filters.query))
            refresh(True)
            state.notify(
                f"Búsqueda: {state.filters.query}"
                if state.filters.query
                else "Búsqueda limpiada.",
                "info",
                ttl=3,
            )
        elif key == ord("?"):
            help_modal(stdscr)
        elif key == 27:
            state.clear_filters()
            state.focus = "list"
            refresh(True)
            state.notify("Filtros limpiados.", "info", ttl=3)
        elif key == curses.KEY_RESIZE:
            continue
        elif key == curses.KEY_MOUSE:
            _handle_mouse(state, layout)

        # Los diálogos consumen sus propias teclas: al volver hay que repintar.
        dirty = True


def run(
    statuses: list[str] | None = None,
    project: str | None = None,
    query: str | None = None,
    limit: int = 300,
    refresh: float = 2.0,
    order: str = "code",
) -> int:
    """Punto de entrada de la TUI. Devuelve el código de salida."""
    try:
        curses.wrapper(
            main_loop, statuses, project, query, limit, refresh, order
        )
    except KeyboardInterrupt:
        return 130
    return 0
