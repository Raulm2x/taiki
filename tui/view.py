"""Dibujo curses de la TUI."""

from __future__ import annotations

import curses
import os
from dataclasses import dataclass
from typing import Iterable

from ticketlib import agents
from ticketlib.util import search_tokens

from . import heatmap
from .detail import Line, activity_lines, detail_lines, highlight_spans
from .state import (
    ACCENT,
    GREY,
    PANE_LABELS,
    PRIORITY_CURSES,
    SCOPE_LABELS,
    STATUS_BOLD,
    STATUS_CURSES,
    TuiState,
    column_spans,
    display_code,
    muted_style,
    table_width,
)

HELP_LINES = (
    "Atajos de la TUI de tickets",
    "",
    "  espacio           Marcar o desmarcar ('completado'/'verificado')",
    "  enter             Desplegar la épica o marcar la tarea",
    "  a / A             Marcar todos los confirmables / limpiar",
    "  c                 Confirmar la selección (modal)",
    "  m                 Menú de acciones del ticket actual",
    "  o                 Abrir la sesión del agente",
    "  tab               Cambiar el foco entre lista y detalle",
    "  d                 Mostrar u ocultar el panel de detalle",
    "  → / l  ← / h      Desplegar / plegar la épica (← en una tarea va al padre)",
    "  z / Z             Plegar / desplegar todas las épicas",
    "  v                 Alternar detalle / actividad / mapa",
    "  t                 Métrica del mapa: eventos / creados / completados",
    "  h / l             Desplazar semanas en el mapa (← →)",
    "  j k ↑ ↓           Mover el cursor (o el panel con foco)",
    "  g G / PgUp PgDn   Ir al inicio, final o página",
    "  /                 Buscar en título, cuerpo y etiquetas",
    "  f                 Filtros: estado, prioridad, agente, proyecto, tag",
    "  x                 Limpiar filtros",
    "  s                 Ciclar ámbito: atención → todos → completados → abiertos → cerrados",
    "  M                 Alternar vista de cerrados (vuelve al ámbito anterior)",
    "  S                 Estado de sesiones, claims y solapamientos",
    "  O                 Cambiar el orden: código / prioridad",
    "  n                 Cargar más tickets (al llegar al final)",
    "  r                 Recargar desde la base de datos",
    "  R                 Pausar o activar el auto-refresh",
    "  ?                 Esta ayuda",
    "  q                 Salir",
    "",
    "  Ratón: clic en la casilla marca, clic en AGENTE abre la sesión",
    "  La lista se refresca sola cada pocos segundos (--refresh N, 0 = off)",
    "",
    "  Pulsa cualquier tecla para cerrar",
)

HEADERS = {
    "toggle": "[ ]",
    "code": "CÓDIGO",
    "status": "ESTADO",
    "priority": "PRIOR.",
    "kind": "TIPO",
    "project": "PROYECTO",
    "agent": "AGENTE",
    "title": "TÍTULO",
}

FULL_COLUMNS_MIN = 134
DETAIL_MIN_TERM = 108

_REDRAW = None


def set_redraw(callback) -> None:
    """Registra el repintado del fondo para los diálogos."""
    global _REDRAW
    _REDRAW = callback


def _repaint() -> None:
    if _REDRAW is not None:
        _REDRAW()


@dataclass(frozen=True)
class Layout:
    """Geometría de un fotograma, compartida por dibujo y ratón."""

    width: int
    height: int
    compact: bool
    detail_visible: bool
    detail_x: int
    detail_width: int
    columns_y: int = 2
    list_top: int = 4
    list_height: int = 1

    def spans(self) -> list[tuple[str, int, int]]:
        return column_spans(self.compact)

    def column_at(self, x: int) -> str:
        spans = self.spans()
        for name, start, cell_width in spans:
            end = start + cell_width if cell_width else self.detail_x - 1
            if start <= x < end:
                return name
        return "title"


def geometry(width: int, height: int, state: TuiState) -> Layout:
    compact = width < FULL_COLUMNS_MIN
    base = table_width(compact)
    detail_visible = state.show_detail and width - base - 3 >= 46
    if detail_visible:
        detail_width = min(58, max(44, width - base - 27))
        detail_x = width - detail_width
        if detail_x - base - 2 < 16:
            detail_visible = False
    if not detail_visible:
        detail_width = 0
        detail_x = width
    list_top = 4
    list_height = max(1, height - list_top - 2)
    return Layout(
        width=width,
        height=height,
        compact=compact,
        detail_visible=detail_visible,
        detail_x=detail_x,
        detail_width=detail_width,
        list_height=list_height,
    )


class Palette:
    """Pares de color de curses creados bajo demanda."""

    def __init__(self) -> None:
        self.enabled = False
        self.max_colors = 8
        self._pairs: dict[tuple[int, bool, bool], int] = {}
        if os.environ.get("NO_COLOR") or not curses.has_colors():
            return
        try:
            curses.start_color()
            curses.use_default_colors()
            self.enabled = True
            self.max_colors = curses.COLORS
        except curses.error:
            self.enabled = False

    def _fit(self, color: int) -> int:
        if self.max_colors >= 256:
            return max(0, min(color, 255))
        if self.max_colors >= 16:
            return color % 16
        return color % 8

    def attr(self, color: int | None, bold: bool = False, dim: bool = False) -> int:
        base = 0
        if bold:
            base |= curses.A_BOLD
        if dim:
            base |= curses.A_DIM
        if not self.enabled or color is None:
            return base
        key = (self._fit(color), bold, dim)
        pair = self._pairs.get(key)
        if pair is None:
            index = len(self._pairs) + 1
            try:
                curses.init_pair(index, key[0], -1)
            except curses.error:
                return base
            pair = curses.color_pair(index)
            self._pairs[key] = pair
        return pair | base

    def status(self, status: str) -> int:
        return self.attr(STATUS_CURSES.get(status, 7), bold=status in STATUS_BOLD)

    def priority(self, priority: str) -> int:
        return self.attr(PRIORITY_CURSES.get(priority, 7), bold=priority == "urgente")

    def agent(self, name: str | None, has_session: bool) -> int:
        if not name or not has_session:
            return self.attr(GREY, dim=True)
        return self.attr(int(agents.agent_color(name)))

    def toast(self, level: str) -> int:
        if level == "error":
            return self.attr(1, bold=True)
        if level == "ok":
            return self.attr(2, bold=True)
        return self.attr(ACCENT, bold=True)

    # Colores del mapa de actividad (verdes de GitHub en 256 colores).
    HEAT_COLORS = (242, 22, 28, 34, 46)

    def heat(self, level: int, selected: bool = False) -> int:
        """Atributo de una celda del mapa según su nivel 0-4."""
        level = max(0, min(int(level), 4))
        if not self.enabled:
            base = curses.A_REVERSE if selected else 0
            return base
        if self.max_colors >= 256:
            attr = self.attr(self.HEAT_COLORS[level], bold=level >= 4)
        else:
            # 8/16 colores: verde con intensidad por brillo.
            attr = self.attr(2 if level else GREY, bold=level >= 3, dim=level in (1, 2))
        if selected:
            attr |= curses.A_REVERSE
        return attr


def put(win, y: int, x: int, text: str, attr: int = 0) -> None:
    """addstr tolerante a los bordes de la ventana."""
    try:
        win.addstr(y, x, text, attr)
    except curses.error:
        pass


def clear_line(win, y: int, width: int) -> None:
    put(win, y, 0, " " * max(0, width - 1))


def put_marked(
    win,
    y: int,
    x: int,
    text: str,
    attr: int,
    tokens: list[str],
    width: int,
    mark_attr: int,
) -> None:
    """Dibuja ``text`` resaltando las coincidencias de ``tokens``."""
    text = text[: max(0, width)]
    spans = highlight_spans(text, tokens)
    if not spans:
        put(win, y, x, text.ljust(width), attr)
        return
    col = x
    pos = 0
    for start, end in spans:
        if start > pos:
            put(win, y, col, text[pos:start], attr)
            col += start - pos
        put(win, y, col, text[start:end], attr | mark_attr)
        col += end - start
        pos = end
    if pos < len(text):
        put(win, y, col, text[pos:], attr)
        col += len(text) - pos
    put(win, y, col, " " * max(0, width - (col - x)), attr)


def draw_header(
    win, layout: Layout, state: TuiState, interval: float, palette: Palette
) -> None:
    width = layout.width
    title = f" {state.workspace_label or 'Tickets'} "
    put(win, 0, 0, title, curses.A_BOLD | curses.A_REVERSE)

    stats = state.stats or {}
    by_status = stats.get("por_estado") or {}
    confirmable = by_status.get("completado", 0) + by_status.get("verificado", 0)
    pieces = [
        f"abiertos {stats.get('abiertos', 0)}",
        f"en progreso {by_status.get('en_progreso', 0)}",
        f"por confirmar {confirmable}",
        f"urgentes {stats.get('urgentes_pendientes', state.urgent_pending())}",
        f"bloqueados {by_status.get('bloqueado', 0)}",
        f"sin asignar {stats.get('sin_agente', 0)}",
    ]
    info = ""
    for piece in pieces:
        candidate = piece if not info else f"{info} · {piece}"
        if len(title) + 1 + len(candidate) > width - 1:
            break
        info = candidate
    put(win, 0, len(title), (" " + info)[: max(0, width - len(title) - 1)])

    active = state.filters.active()
    visible = len(state.visible())
    total = len(state.tickets)
    filters = (
        "filtros: " + " · ".join(f"{name}={value}" for name, value in active)
        if active
        else "sin filtros"
    )
    line = (
        f" {visible}/{total} visibles · ámbito: "
        f"{SCOPE_LABELS.get(state.filters.scope, state.filters.scope)}"
    )
    scope_hidden = state.scope_hidden()
    if scope_hidden:
        line += f" ({scope_hidden} oculto(s))"
    line += f" · {filters} · orden: {state.filters.order}"
    hidden = state.hidden_count()
    if hidden:
        line += f" · {hidden} plegada(s)"
    if state.truncated:
        line += " · hay más (n)"
    put(win, 1, 0, line[: max(0, width - 1)], curses.A_DIM)
    if state.mcp_orphans:
        warning = f"⚠ {state.mcp_orphans} MCP huérfano(s) (taiki servers --prune) "
    elif state.mcp_stale:
        warning = "⚠ MCP desactualizado (recarga al quedar idle) "
        if len(line) + len(warning) + 2 <= width:
            put(
                win,
                1,
                width - len(warning) - 1,
                warning,
                palette.attr(9, bold=True),
            )


def draw_columns(win, layout: Layout, state: TuiState) -> None:
    attr = curses.A_BOLD | (curses.A_REVERSE if state.focus == "list" else 0)
    for name, x, cell_width in layout.spans():
        header = HEADERS[name]
        text = header[:cell_width] if cell_width else header
        put(win, layout.columns_y, x, text, attr)
    if layout.detail_visible:
        panel = PANE_LABELS.get(state.pane, "PANEL")
        if state.focus == "detail":
            panel = f" {panel} "
        focused = state.focus == "detail"
        put(
            win,
            layout.columns_y,
            layout.detail_x,
            panel[: layout.detail_width - 1],
            curses.A_BOLD | (curses.A_REVERSE if focused else curses.A_DIM),
        )


def draw_rows(win, layout: Layout, state: TuiState, palette: Palette) -> None:
    spans = {name: (x, w) for name, x, w in layout.spans()}
    tokens = search_tokens(state.filters.query)
    highlight = palette.attr(ACCENT, bold=True) | curses.A_UNDERLINE
    rows = state.rows()
    state.ensure_visible(layout.list_height)
    table_end = layout.detail_x - 1

    for offset in range(layout.list_height):
        index = state.scroll + offset
        y = layout.list_top + offset
        if index >= len(rows):
            clear_line(win, y, table_end)
            continue
        ticket, depth, epic = rows[index]
        selected = index == state.cursor
        row_attr = curses.A_REVERSE if selected else 0
        dim, strike = muted_style(ticket["status"])
        if selected:
            # La selección manda: atenuar + invertir dejaba el texto ilegible.
            dim = strike = False
        if dim:
            row_attr |= curses.A_DIM
        if strike:
            # ncurses no siempre expone el tachado; si no existe, solo atenúa.
            row_attr |= getattr(curses, "A_STRIKETHROUGH", 0) or getattr(
                curses, "A_ITALIC", 0
            )
        clear_line(win, y, table_end)

        if state.can_confirm(ticket):
            mark = "[x]" if ticket["code"] in state.selected else "[ ]"
            toggle_attr = curses.A_BOLD if mark == "[x]" else 0
        else:
            mark = "[·]"
            toggle_attr = curses.A_DIM
        x, w = spans["toggle"]
        put(win, y, x, mark[:w], toggle_attr | row_attr)

        code = ticket["code"]
        fresh = state.is_fresh(code)
        prefix = "*" if fresh else " "
        x, w = spans["code"]
        code_attr = curses.A_BOLD | row_attr
        if fresh:
            code_attr |= palette.attr(ACCENT, bold=True)
        put_marked(
            win,
            y,
            x,
            prefix + display_code(code, depth),
            code_attr,
            tokens,
            w,
            highlight,
        )

        x, w = spans["status"]
        put(
            win,
            y,
            x,
            ticket["status"][:w].ljust(w),
            palette.status(ticket["status"]) | row_attr,
        )

        x, w = spans["priority"]
        put(
            win,
            y,
            x,
            ticket["priority"][:w].ljust(w),
            palette.priority(ticket["priority"]) | row_attr,
        )

        if not layout.compact:
            x, w = spans["kind"]
            put(win, y, x, ticket["kind"][:w].ljust(w), row_attr)
            x, w = spans["project"]
            put(win, y, x, (ticket.get("project") or "-")[:w].ljust(w), row_attr)

        x, w = spans["agent"]
        agent_name = agents.agent_name(ticket)
        has_session = bool(state.session_of(ticket))
        put(
            win,
            y,
            x,
            (agent_name or "-")[:w].ljust(w),
            palette.agent(agent_name, has_session) | row_attr,
        )

        x, _ = spans["title"]
        available = max(0, table_end - x)
        prefix = ""
        if epic:
            prefix = "▾ " if state.is_expanded(ticket["code"]) else "▸ "
        elif depth:
            prefix = "  " * depth
        progress = ticket.get("progress") or {}
        badge = ""
        if progress.get("total"):
            badge = f"[{progress['done']}/{progress['total']}] "
        title = prefix + badge + ticket["title"]
        if len(title) > available:
            title = title[: max(0, available - 1)] + "…"
        put_marked(win, y, x, title, row_attr, tokens, available, highlight)


def _last_word(text: str) -> str:
    parts = text.split()
    return parts[-1] if parts else ""


def _draw_line(
    win,
    y: int,
    x: int,
    width: int,
    line: Line,
    palette: Palette,
    tokens: list[str],
    mark: int,
) -> None:
    attrs = {
        "title": curses.A_BOLD,
        "label": curses.A_BOLD | palette.attr(ACCENT),
        "value": 0,
        "dim": curses.A_DIM,
        "accent": palette.attr(ACCENT),
        "status": palette.status(_last_word(line.text)),
        "priority": palette.priority(_last_word(line.text)),
    }
    attr = attrs.get(line.style, 0)
    put_marked(win, y, x, line.text, attr, tokens, width, mark)


def draw_panel(win, layout: Layout, state: TuiState, palette: Palette) -> None:
    if not layout.detail_visible:
        return
    x = layout.detail_x
    width = layout.detail_width
    top = layout.columns_y
    bottom = layout.height - 2
    for y in range(top, bottom):
        put(win, y, x - 2, "│", curses.A_DIM)

    ticket = state.detail or state.current()
    tokens = search_tokens(state.filters.query)
    highlight = palette.attr(ACCENT, bold=True) | curses.A_UNDERLINE
    if state.pane == "heatmap":
        _draw_heatmap(win, layout, state, palette)
        return
    if state.pane == "detail":
        session_id = state.session_of(ticket) if ticket else None
        session_dir = state.session_dir_of(ticket) if ticket else None
        lines = detail_lines(ticket, width, session_id, session_dir)
    else:
        lines = activity_lines(state.activity, width)

    available = max(0, bottom - layout.list_top)
    state.scroll_panel(0, available, len(lines))
    visible = lines[state.detail_scroll : state.detail_scroll + available]
    for offset in range(available):
        y = layout.list_top + offset
        if offset < len(visible):
            _draw_line(
                win, y, x, width - 1, visible[offset], palette, tokens, highlight
            )
        else:
            clear_line(win, y, x)
    if len(lines) > available:
        hint = (
            f" {state.detail_scroll + 1}-"
            f"{min(len(lines), state.detail_scroll + available)}/{len(lines)} "
        )
        put(
            win,
            layout.columns_y,
            max(x, x + width - len(hint)),
            hint,
            curses.A_DIM,
        )


def _draw_heatmap(win, layout: Layout, state: TuiState, palette: Palette) -> None:
    """Dibuja el mapa de actividad en el panel lateral."""
    x = layout.detail_x
    width = max(8, layout.detail_width - 1)
    top = layout.list_top
    bottom = layout.height - 2
    available = max(0, bottom - top)

    view = heatmap.render_map(
        state.heatmap,
        metric=state.heatmap_metric,
        width=width,
        selected=state.heatmap_day(),
    )
    lines = view.lines
    for offset in range(available):
        y = top + offset
        if offset < len(lines):
            draw_segments(win, y, x, width, lines[offset], palette)
        else:
            clear_line(win, y, x)

    if len(lines) > available and available:
        put(
            win,
            top + available - 1,
            x,
            " …".ljust(width)[:width],
            curses.A_DIM,
        )


def draw_segments(
    win,
    y: int,
    x: int,
    width: int,
    segments: list[heatmap.Segment],
    palette: Palette,
) -> None:
    """Dibuja una línea compuesta por segmentos de distinto estilo."""
    column = x
    room = max(0, width)
    for segment in segments:
        if room <= 0:
            break
        text = segment.text[:room]
        if not text:
            continue
        attr = _segment_attr(segment.style, palette)
        put(win, y, column, text, attr)
        column += len(text)
        room -= len(text)


def _segment_attr(style: str, palette: Palette) -> int:
    """Traduce el estilo simbólico de un segmento a un atributo curses."""
    if style.startswith("heat"):
        name, _, extra = style.partition("_")
        try:
            value = int(name[4:])
        except ValueError:
            value = 0
        return palette.heat(value, selected=extra == "sel")
    return {
        "label": curses.A_BOLD,
        "accent": palette.attr(ACCENT),
        "dim": curses.A_DIM,
        "value": 0,
    }.get(style, 0)


def draw_statusbar(
    win,
    layout: Layout,
    state: TuiState,
    interval: float,
    palette: Palette,
) -> None:
    width = layout.width
    height = layout.height
    toast = state.active_toast()
    clear_line(win, height - 2, width)
    if toast is not None:
        prefix = {"error": " ! ", "ok": " ✓ ", "info": " · "}.get(toast.level, " · ")
        text = (prefix + toast.text)[: width - 1]
        put(win, height - 2, 0, text, palette.toast(toast.level))

    pending = len(state.selected_codes())
    focus = "detalle" if state.focus == "detail" else "lista"
    if width >= 110:
        help_text = (
            f" espacio marcar · c confirmar ({pending}) · m acciones · o sesión · "
            f"f filtros · tab foco ({focus}) · v panel · ? ayuda · q salir"
        )
    else:
        help_text = (
            f" espacio · c confirmar ({pending}) · m acciones · f filtros · "
            "? ayuda · q salir"
        )
    auto = ""
    if interval > 0:
        auto = f" auto {interval:g}s " if state.auto_refresh else " auto pausa "
    room = max(0, width - len(auto))
    text = help_text
    if len(text) > room:
        text = text[: max(0, room - 1)] + "…"
    put(win, height - 1, 0, text.ljust(room), curses.A_REVERSE)
    if auto:
        put(
            win,
            height - 1,
            room,
            auto,
            curses.A_REVERSE | curses.A_BOLD,
        )
    put(win, height - 1, width - 1, " ", curses.A_REVERSE)


def _band(win, top: int, height: int, width: int) -> None:
    """Limpia la franja que ocupará un diálogo (fondo sólido y limpio)."""
    for row in range(top, top + height):
        if row < 0:
            continue
        clear_line(win, row, width)


def draw_box(win, lines: list[str], width: int | None = None, title: str = "") -> None:
    height, full_width = win.getmaxyx()
    lines = lines[: max(0, height - 3)]
    longest = max([len(line) for line in lines] + [len(title)])
    box_width = width or longest + 4
    box_width = min(max(box_width, 24), full_width - 2)
    box_height = len(lines) + 3
    top = max(0, (height - box_height) // 2)
    left = max(0, (full_width - box_width) // 2)

    _repaint()
    _band(win, top, box_height, full_width)
    put(win, top, left, "+" + "-" * (box_width - 2) + "+")
    if title:
        label = f" {title} "
        put(win, top, left + 1, label[: box_width - 3], curses.A_BOLD)
    blank = " " * (box_width - 2)
    for index in range(box_height - 2):
        put(win, top + 1 + index, left, "|" + blank + "|")
    put(win, top + box_height - 1, left, "+" + "-" * (box_width - 2) + "+")

    for index, line in enumerate(lines):
        put(win, top + 1 + index, left + 2, line[: box_width - 4])
    win.refresh()


def modal(
    win,
    lines: list[str],
    keys: Iterable[int] | None = None,
    title: str = "",
) -> int:
    """Muestra un cuadro y espera una tecla.

    Sin ``keys`` se cierra con cualquier tecla; con ``keys`` solo esas teclas
    confirman y ``n``/``q``/Esc cancelan.
    """
    win.timeout(-1)
    draw_box(win, lines, title=title)
    if keys is None:
        while win.getch() == -1:
            pass
        return 0
    allowed = set(keys)
    while True:
        key = win.getch()
        if key == -1:
            return 27
        if key in allowed:
            return key
        if key in (27, ord("n"), ord("N"), ord("q")):
            return 27


def menu(
    win,
    title: str,
    items: list[str],
    current: int = 0,
    hint: str = "j/k mover · enter elegir · esc cancelar",
) -> int | None:
    """Menú vertical; devuelve el índice elegido o ``None``."""
    if not items:
        return None
    win.timeout(-1)
    index = max(0, min(current, len(items) - 1))
    height, full_width = win.getmaxyx()
    box_width = min(
        max([len(item) for item in items] + [len(title), len(hint)]) + 6,
        full_width - 2,
    )
    box_height = len(items) + 4
    top = max(0, (height - box_height) // 2)
    left = max(0, (full_width - box_width) // 2)
    inner = box_width - 4

    while True:
        _repaint()
        _band(win, top, box_height, full_width)
        put(win, top, left, "+" + "-" * (box_width - 2) + "+")
        put(win, top, left + 1, f" {title} "[: inner + 1], curses.A_BOLD)
        for row in range(len(items)):
            attr = curses.A_REVERSE | curses.A_BOLD if row == index else 0
            text = f" {items[row]} "[:inner].ljust(inner)
            put(win, top + 1 + row, left + 1, "|" + text + "|", attr)
        put(win, top + 1 + len(items), left, "|" + " " * (box_width - 2) + "|")
        hint_text = f" {hint} "[:inner].ljust(inner)
        put(win, top + 2 + len(items), left, "|" + hint_text + "|")
        put(win, top + 3 + len(items), left, "+" + "-" * (box_width - 2) + "+")
        win.refresh()

        key = win.getch()
        if key in (27, ord("q")):
            return None
        if key in (10, 13, curses.KEY_ENTER):
            return index
        if key in (curses.KEY_UP, ord("k")):
            index = (index - 1) % len(items)
        elif key in (curses.KEY_DOWN, ord("j")):
            index = (index + 1) % len(items)
        elif ord("1") <= key <= ord("9"):
            candidate = key - ord("1")
            if candidate < len(items):
                return candidate


def choose(
    win,
    title: str,
    options: list[str],
    current: str | None = None,
) -> tuple[bool, str | None]:
    """Elige un valor; devuelve ``(elegido, valor)``.

    La primera opción es siempre ``(todos)``, que devuelve ``(True, None)``.
    Cancelar devuelve ``(False, None)``.
    """
    items = ["(todos)"] + options
    start = items.index(current) if current in items else 0
    picked = menu(win, title, items, start)
    if picked is None:
        return False, None
    return True, None if picked == 0 else items[picked]


def help_modal(win) -> None:
    modal(win, list(HELP_LINES), title="Ayuda")


def status_modal(win, lines: list[str]) -> None:
    """Modal con el estado de sesiones, claims y solapamientos."""
    modal(win, list(lines), title="Sesiones y claims")


def prompt(win, label: str, initial: str = "") -> str:
    """Pide una línea de texto en la barra inferior."""
    height, width = win.getmaxyx()
    win.timeout(-1)
    _repaint()
    curses.curs_set(1)
    curses.echo()
    try:
        put(win, height - 1, 0, " " * (width - 1))
        put(win, height - 1, 0, label[: width - 1], curses.A_BOLD)
        start = min(len(label), width - 1)
        put(win, height - 1, start, initial[: max(0, width - start - 1)])
        raw = win.getstr(height - 1, start + len(initial), max(1, width - start - 2))
    except curses.error:
        raw = b""
    finally:
        curses.noecho()
        curses.curs_set(0)
    return raw.decode("utf-8", "replace").strip()


def confirm_dialog(win, title: str, lines: list[str]) -> bool:
    body = list(lines) + ["", "  [s] Confirmar     [n/Esc] Cancelar"]
    return modal(win, body, (ord("s"), ord("S")), title=title) != 27


def draw_too_small(win, width: int, height: int, minimum: int) -> None:
    win.erase()
    put(
        win,
        0,
        0,
        f"Terminal demasiado pequeño ({width}x{height}). Mínimo {minimum}x8. "
        "q para salir.",
        curses.A_BOLD,
    )
    put(
        win,
        1,
        0,
        "Amplía la ventana o usa `taiki list` mientras tanto.",
        curses.A_DIM,
    )
    win.refresh()
