"""Estado y geometría de la TUI (sin curses, testeable)."""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any, Iterable

from ticketlib import agents
from ticketlib.domain import FINAL_STATUSES, OPEN_STATUSES

from . import heatmap

# `atencion` es el ámbito por defecto: todo lo que aún requiere acción, incluidos
# `completado` y `verificado` (pendientes de confirmar), pero sin `confirmado`
# ni `cancelado`.
SCOPES = ("atencion", "todos", "completados", "abiertos", "cerrados")
DEFAULT_SCOPE = "atencion"
# Ámbitos que no son un filtro manual: conservan el árbol de épicas.
TREE_SCOPES = (DEFAULT_SCOPE, "todos")
SCOPE_LABELS = {
    "atencion": "atención",
    "todos": "todos",
    "completados": "completados",
    "abiertos": "abiertos",
    "cerrados": "cerrados",
}
SCOPE_STATUSES: dict[str, tuple[str, ...] | None] = {
    "atencion": None,
    "todos": None,
    "completados": ("completado", "verificado"),
    "abiertos": tuple(OPEN_STATUSES),
    "cerrados": tuple(FINAL_STATUSES),
}
SCOPE_EXCLUDED: dict[str, tuple[str, ...]] = {
    "atencion": tuple(FINAL_STATUSES),
}
FILTERS = SCOPES
FILTER_STATUSES = {
    k: list(v) if v is not None else None for k, v in SCOPE_STATUSES.items()
}
# Estados que el usuario puede marcar para confirmar desde la TUI.
CONFIRMABLE = ("completado", "verificado")
ORDER_MODES = ("code", "priority")
FOCUSES = ("list", "detail")
PANES = ("detail", "activity", "heatmap")
PANE_LABELS = {
    "detail": "DETALLE",
    "activity": "ACTIVIDAD",
    "heatmap": "MAPA",
}
PANE_MESSAGES = {
    "detail": "Detalle del ticket.",
    "activity": "Actividad reciente.",
    "heatmap": "Mapa de actividad.",
}

COLUMNS: tuple[tuple[str, int], ...] = (
    ("toggle", 3),
    ("code", 7),
    ("status", 11),
    ("priority", 9),
    ("kind", 13),
    ("project", 9),
    ("agent", 13),
    ("title", 0),
)
# En modo dividido el tipo y el proyecto viven en el panel de detalle.
COMPACT_COLUMNS: tuple[tuple[str, int], ...] = (
    ("toggle", 3),
    ("code", 7),
    ("status", 11),
    ("priority", 9),
    ("agent", 13),
    ("title", 0),
)
DETAIL_MIN_WIDTH = 118
DETAIL_FORCE_WIDTH = 104
DETAIL_MAX_WIDTH = 58
DETAIL_MIN_PANEL = 42
FRESH_SECONDS = 6.0

STATUS_CURSES = {
    "nuevo": 6,
    "pendiente": 3,
    "en_progreso": 4,
    "completado": 2,
    "verificado": 2,
    "confirmado": 2,
    "bloqueado": 5,
    "cancelado": 8,
}
STATUS_BOLD = {"confirmado"}
PRIORITY_CURSES = {"urgente": 1, "alta": 1, "media": 3, "baja": 8}
GREY = 8
ACCENT = 6


@dataclass
class Filters:
    """Filtros combinables de la lista."""

    scope: str = DEFAULT_SCOPE
    status: str | None = None
    priority: str | None = None
    agent: str | None = None
    project: str | None = None
    tag: str | None = None
    kind: str | None = None
    order: str = "code"
    query: str = ""
    search_codes: set[str] | None = None

    def clear(self) -> None:
        """Limpia los filtros, conservando el orden."""
        order = self.order
        self.__dict__.update(Filters().__dict__)
        self.order = order

    def active(self) -> list[tuple[str, str]]:
        """Pares (etiqueta, valor) de los filtros activos.

        El ámbito por defecto (``atencion``) no se considera un filtro manual:
        no aplana el árbol ni se anuncia como filtro, porque la barra de estado
        ya muestra el ámbito siempre.
        """
        pairs = []
        if self.scope not in TREE_SCOPES:
            pairs.append(("estado", self.scope))
        for name in ("status", "priority", "agent", "project", "tag", "kind"):
            value = getattr(self, name)
            if value:
                pairs.append((name, value))
        if self.query:
            pairs.append(("texto", self.query))
        return pairs

    def in_scope(self, ticket: dict[str, Any]) -> bool:
        """True si el ticket cae dentro del ámbito activo."""
        statuses = SCOPE_STATUSES.get(self.scope)
        if statuses is not None and ticket["status"] not in statuses:
            return False
        excluded = SCOPE_EXCLUDED.get(self.scope)
        if excluded and ticket["status"] in excluded:
            return False
        return True

    def matches(
        self, ticket: dict[str, Any], *, in_scope: bool | None = None
    ) -> bool:
        if in_scope is None:
            in_scope = self.in_scope(ticket)
        if not in_scope:
            return False
        if self.status and ticket["status"] != self.status:
            return False
        if self.priority and ticket["priority"] != self.priority:
            return False
        if self.kind and ticket["kind"] != self.kind:
            return False
        if self.project and (ticket.get("project") or "") != self.project:
            return False
        if self.agent and agents.agent_name(ticket) != self.agent:
            return False
        if self.tag and self.tag not in (ticket.get("tags") or []):
            return False
        needle = self.query.strip().lower()
        if needle:
            haystack = " ".join(
                (
                    ticket["code"],
                    ticket["title"],
                    ticket.get("body") or "",
                    ticket.get("project") or "",
                    agents.agent_name(ticket),
                    " ".join(ticket.get("tags") or []),
                )
            ).lower()
            if needle not in haystack:
                # FTS + comentarios: el SQL decide el resto de coincidencias.
                if not (
                    self.search_codes and ticket["code"] in self.search_codes
                ):
                    return False
        return True


@dataclass
class Toast:
    """Mensaje efímero de la barra inferior."""

    text: str
    level: str = "info"
    created: float = field(default_factory=time.monotonic)
    ttl: float = 5.0

    def alive(self, now: float | None = None) -> bool:
        return (now or time.monotonic()) - self.created < self.ttl


def column_spans(compact: bool = False) -> list[tuple[str, int, int]]:
    """Posiciones (nombre, x, ancho) de cada columna visible."""
    spans: list[tuple[str, int, int]] = []
    x = 0
    for name, width in COMPACT_COLUMNS if compact else COLUMNS:
        if width:
            spans.append((name, x, width))
            x += width + 1
    spans.append(("title", x, 0))
    return spans


def layout_width(compact: bool = False) -> int:
    """Ancho mínimo razonable para que quepa el título."""
    return column_spans(compact)[-1][1] + 6


MUTED_STATUSES = ("confirmado", "cancelado")


def muted_style(status: str) -> tuple[bool, bool]:
    """(atenuar, tachar) para tickets ya cerrados.

    Cancelado pide tachado; si el curses/terminal no lo soporta, el llamador
    solo aplica la atenuación y el color del estado.
    """
    if status == "cancelado":
        return True, True
    if status == "confirmado":
        return True, False
    return False, False


def display_code(code: str, depth: int) -> str:
    """Código visible en el árbol: las tareas muestran solo ``.01``."""
    if depth and "." in code:
        return "." + code.rsplit(".", 1)[1]
    return code


def table_width(compact: bool = False) -> int:
    """Ancho ocupado por las columnas fijas de la tabla."""
    return column_spans(compact)[-1][1]


class _FocusStr(str):
    def __new__(cls, value: str, state: Any):
        obj = super().__new__(cls, value)
        obj._state = state
        return obj

    def __call__(self, code: str) -> bool:
        return self._state.focus_on(code)


@dataclass
class TuiState:
    """Estado puro de la TUI: datos, filtros, selección, foco y avisos."""

    tickets: list[dict[str, Any]] = field(default_factory=list)
    session_map: dict[str, str | None] = field(default_factory=dict)
    session_dirs: dict[str, str | None] = field(default_factory=dict)
    stats: dict[str, Any] = field(default_factory=dict)
    workspace_label: str = ""
    activity: list[dict[str, Any]] = field(default_factory=list)
    filters: Filters = field(default_factory=Filters)
    selected: set[str] = field(default_factory=set)
    cursor: int = 0
    scroll: int = 0
    truncated: bool = False
    mcp_stale: bool = False
    mcp_orphans: int = 0
    limit: int = 300
    auto_refresh: bool = True
    show_detail: bool = True
    scope_before_closed: str = DEFAULT_SCOPE
    _focus: str = "list"
    pane: str = "detail"
    detail_scroll: int = 0
    heatmap: dict[str, dict[str, int]] = field(default_factory=dict)
    heatmap_metric: str = "eventos"
    heatmap_cursor: str | None = None
    toasts: list[Toast] = field(default_factory=list)
    fresh: dict[str, float] = field(default_factory=dict)
    detail: dict[str, Any] | None = None
    detail_key: tuple | None = None
    expanded: set[str] = field(default_factory=set)

    @property
    def focus(self) -> _FocusStr:
        return _FocusStr(self._focus, self)

    @focus.setter
    def focus(self, value: str) -> None:
        self._focus = str(value)

    # ── Carga y consultas ────────────────────────────────────────────────
    def load(
        self,
        tickets: Iterable[dict[str, Any]],
        session_map: dict[str, str | None] | None = None,
        keep_code: str | None = None,
        truncated: bool | None = None,
        stats: dict[str, Any] | None = None,
        activity: Iterable[dict[str, Any]] | None = None,
        search_codes: set[str] | None = None,
        mark_fresh: bool = True,
        session_dirs: dict[str, str | None] | None = None,
        mcp_stale: bool | None = None,
        mcp_orphans: int | None = None,
        heatmap: dict[str, dict[str, int]] | None = None,
    ) -> None:
        """Reemplaza los datos conservando el cursor sobre ``keep_code``."""
        if keep_code is None:
            current = self.current()
            keep_code = current["code"] if current else None
        previous = {ticket["code"]: ticket for ticket in self.tickets}
        self.tickets = list(tickets)
        self.session_map = dict(session_map or {})
        self.session_dirs = dict(session_dirs or {})
        if truncated is not None:
            self.truncated = truncated
        if mcp_stale is not None:
            self.mcp_stale = mcp_stale
        if mcp_orphans is not None:
            self.mcp_orphans = mcp_orphans
        if heatmap is not None:
            self.heatmap = dict(heatmap)
        if stats is not None:
            self.stats = stats
        if activity is not None:
            self.activity = list(activity)
        self.filters.search_codes = search_codes
        if mark_fresh and previous:
            self._mark_changes(previous)
        codes = {ticket["code"] for ticket in self.tickets}
        self.selected &= codes
        epics = {
            ticket["code"] for ticket in self.tickets if self.is_epic(ticket)
        }
        self.expanded &= epics
        self.clamp()
        if keep_code:
            self.focus_on(keep_code)

    def _mark_changes(self, previous: dict[str, dict[str, Any]]) -> None:
        now = time.monotonic()
        for ticket in self.tickets:
            old = previous.get(ticket["code"])
            if old is None:
                self.fresh[ticket["code"]] = now
            elif any(
                old.get(name) != ticket.get(name)
                for name in ("status", "priority", "title", "assigned_to")
            ):
                self.fresh[ticket["code"]] = now
        current = {ticket["code"] for ticket in self.tickets}
        self.fresh = {
            code: seen for code, seen in self.fresh.items() if code in current
        }

    def matches(self) -> list[dict[str, Any]]:
        parents = {ticket["id"]: ticket for ticket in self.tickets}
        return [
            ticket for ticket in self.tickets if self._matches(ticket, parents)
        ]

    def _matches(
        self, ticket: dict[str, Any], parents: dict[int, dict[str, Any]]
    ) -> bool:
        if self.filters.matches(ticket):
            return True
        if not self._closed_child_visible(ticket, parents):
            return False
        # En atención, un hijo cerrado de un padre activo se muestra, pero el
        # resto de filtros manuales siguen aplicándose.
        return self.filters.matches(ticket, in_scope=True)

    def _closed_child_visible(
        self, ticket: dict[str, Any], parents: dict[int, dict[str, Any]]
    ) -> bool:
        if self.filters.scope != DEFAULT_SCOPE:
            return False
        if ticket["status"] not in FINAL_STATUSES:
            return False
        parent = parents.get(ticket.get("parent_id"))
        return parent is not None and parent["status"] not in FINAL_STATUSES

    def is_epic(self, ticket: dict[str, Any]) -> bool:
        return bool((ticket.get("progress") or {}).get("total"))

    def is_expanded(self, code: str) -> bool:
        return code in self.expanded

    def rows(self) -> list[tuple[dict[str, Any], int, bool]]:
        """Filas visibles ``(ticket, profundidad, es_épica)``.

        Con filtros manuales se muestra la lista plana de coincidencias para no
        esconder tareas; sin ellos, árbol de raíces y tareas desplegadas. El
        ámbito también recorta el conjunto, pero conserva el árbol: un ticket
        cuyo padre queda fuera del ámbito se dibuja como raíz.
        """
        matched = self.matches()
        if self.filters.active():
            return [(ticket, 0, self.is_epic(ticket)) for ticket in matched]

        by_id = {ticket["id"]: ticket for ticket in matched}
        children: dict[int, list[dict[str, Any]]] = {}
        roots: list[dict[str, Any]] = []
        for ticket in matched:
            parent = ticket.get("parent_id")
            if parent is not None and parent in by_id:
                children.setdefault(parent, []).append(ticket)
            else:
                roots.append(ticket)

        rows: list[tuple[dict[str, Any], int, bool]] = []
        for root in roots:
            kids = sorted(children.get(root["id"], []), key=lambda t: t["id"])
            rows.append((root, 0, bool(kids)))
            if kids and root["code"] in self.expanded:
                rows.extend((child, 1, False) for child in kids)
        return rows

    def visible(self) -> list[dict[str, Any]]:
        return [ticket for ticket, _, _ in self.rows()]

    def hidden_count(self) -> int:
        """Tickets que pasan los filtros pero quedan plegados en el árbol."""
        if self.filters.active():
            return 0
        return max(0, len(self.matches()) - len(self.rows()))

    def scope_hidden(self) -> int:
        """Tickets que el ámbito activo deja fuera de la lista."""
        parents = {ticket["id"]: ticket for ticket in self.tickets}
        return sum(
            1
            for ticket in self.tickets
            if not self.filters.in_scope(ticket)
            and not self._closed_child_visible(ticket, parents)
        )

    def attention(self) -> list[dict[str, Any]]:
        """Tickets cargados que aún requieren acción.

        No depende del ámbito elegido: la cabecera mide la carga pendiente real
        (todo salvo ``confirmado`` y ``cancelado``), no el filtro momentáneo.
        """
        return [
            ticket
            for ticket in self.tickets
            if ticket["status"] not in FINAL_STATUSES
        ]

    def urgent_pending(self) -> int:
        """Urgentes que requieren atención (sin confirmados ni cancelados)."""
        return sum(
            1 for ticket in self.attention() if ticket["priority"] == "urgente"
        )

    def toggle_expanded(self, code: str | None = None) -> bool:
        target = self.current() if code is None else next(
            (t for t in self.tickets if t["code"] == code), None
        )
        if target is None or not self.is_epic(target):
            return False
        if target["code"] in self.expanded:
            self.expanded.discard(target["code"])
        else:
            self.expanded.add(target["code"])
        return True

    def expand_all(self) -> int:
        self.expanded = {
            ticket["code"] for ticket in self.tickets if self.is_epic(ticket)
        }
        return len(self.expanded)

    def collapse_all(self) -> None:
        self.expanded.clear()

    def focus_parent(self) -> bool:
        current = self.current()
        if current is None or current.get("parent_id") is None:
            return False
        parent = next(
            (
                ticket
                for ticket in self.tickets
                if ticket["id"] == current["parent_id"]
            ),
            None,
        )
        return self.focus_on(parent["code"]) if parent else False

    def is_fresh(self, code: str, now: float | None = None) -> bool:
        seen = self.fresh.get(code)
        if seen is None:
            return False
        return (now or time.monotonic()) - seen < FRESH_SECONDS

    def can_confirm(self, ticket: dict[str, Any]) -> bool:
        if ticket["status"] not in CONFIRMABLE:
            return False
        progress = ticket.get("progress") or {}
        return not (
            progress.get("total")
            and progress.get("done", 0) < progress["total"]
        )

    def children_of(self, ticket: dict[str, Any]) -> list[dict[str, Any]]:
        """Tareas directas cargadas de una épica."""
        return [
            other
            for other in self.tickets
            if other.get("parent_id") == ticket["id"]
        ]

    def session_of(self, ticket: dict[str, Any]) -> str | None:
        return self.session_map.get(ticket["code"])

    def session_dir_of(self, ticket: dict[str, Any]) -> str | None:
        """Directorio donde abriría la sesión viva del ticket (si lo registra)."""
        return self.session_dirs.get(ticket["code"])

    def confirmable(self) -> list[dict[str, Any]]:
        return [ticket for ticket in self.visible() if self.can_confirm(ticket)]

    def selected_codes(self) -> list[str]:
        """Códigos marcados, incluidas las tareas plegadas y en orden estable.

        Solo cuenta los tickets que pasan los filtros activos: lo oculto no se
        confirma por accidente al cambiar de ámbito.
        """
        codes = [
            ticket["code"]
            for ticket in self.tickets
            if ticket["code"] in self.selected
            and self.can_confirm(ticket)
            and self.filters.matches(ticket)
        ]
        return sorted(codes)

    def agent_options(self) -> list[str]:
        return sorted(
            {
                agents.agent_name(ticket)
                for ticket in self.tickets
                if agents.agent_name(ticket)
            }
        )

    def project_options(self) -> list[str]:
        return sorted(
            {ticket["project"] for ticket in self.tickets if ticket.get("project")}
        )

    def tag_options(self) -> list[str]:
        return sorted(
            {tag for ticket in self.tickets for tag in ticket.get("tags") or []}
        )

    def status_options(self) -> list[str]:
        return sorted({ticket["status"] for ticket in self.tickets})

    # ── Navegación ───────────────────────────────────────────────────────
    def current(self) -> dict[str, Any] | None:
        rows = self.visible()
        if not rows:
            return None
        self.cursor = max(0, min(self.cursor, len(rows) - 1))
        return rows[self.cursor]

    def clamp(self) -> None:
        total = len(self.visible())
        if total == 0:
            self.cursor = 0
            self.scroll = 0
            return
        self.cursor = max(0, min(self.cursor, total - 1))
        self.scroll = max(0, min(self.scroll, total - 1))

    def move(self, delta: int) -> None:
        total = len(self.visible())
        if total == 0:
            return
        self.cursor = max(0, min(self.cursor + delta, total - 1))
        self.detail_scroll = 0

    def top(self) -> None:
        self.cursor = 0
        self.detail_scroll = 0

    def bottom(self) -> None:
        self.cursor = max(0, len(self.visible()) - 1)
        self.detail_scroll = 0

    def focus_on(self, code: str) -> bool:
        """Coloca el cursor sobre ``code`` si sigue visible."""
        for index, ticket in enumerate(self.visible()):
            if ticket["code"] == code:
                self.cursor = index
                return True
        return False

    def ensure_visible(self, height: int) -> None:
        """Ajusta el scroll para que el cursor quede dentro de la ventana."""
        if height <= 0:
            return
        if self.cursor < self.scroll:
            self.scroll = self.cursor
        elif self.cursor >= self.scroll + height:
            self.scroll = self.cursor - height + 1
        self.scroll = max(0, min(self.scroll, max(0, len(self.visible()) - 1)))

    def needs_more(self) -> bool:
        """True si el cursor está al final y quedan tickets por cargar."""
        rows = len(self.visible())
        return self.truncated and rows > 0 and self.cursor >= rows - 1

    # ── Acciones ─────────────────────────────────────────────────────────
    def toggle(self, code: str | None = None) -> bool:
        target = (
            self.current()
            if code is None
            else next((t for t in self.tickets if t["code"] == code), None)
        )
        if target is None or not self.can_confirm(target):
            return False
        key = target["code"]
        if key in self.selected:
            self.selected.discard(key)
            for child in self.children_of(target):
                if self.can_confirm(child):
                    self.selected.discard(child["code"])
        else:
            self.selected.add(key)
            for child in self.children_of(target):
                if self.can_confirm(child):
                    self.selected.add(child["code"])
        return True

    def select_all(self) -> int:
        for ticket in self.tickets:
            if self.can_confirm(ticket) and self.filters.matches(ticket):
                self.selected.add(ticket["code"])
        return len(self.selected)

    def clear_selection(self) -> None:
        self.selected.clear()

    def set_scope(self, scope: str) -> str:
        if scope not in SCOPES:
            raise ValueError(f"Filtro desconocido: {scope}")
        if scope == "cerrados" and self.filters.scope != "cerrados":
            self.scope_before_closed = self.filters.scope
        elif scope != "cerrados":
            self.scope_before_closed = scope
        self.filters.scope = scope
        self.cursor = 0
        self.scroll = 0
        self.clamp()
        return scope

    def cycle_scope(self) -> str:
        index = (SCOPES.index(self.filters.scope) + 1) % len(SCOPES)
        return self.set_scope(SCOPES[index])

    def toggle_closed_scope(self) -> str:
        """Alterna la vista de cerrados con el ámbito que estaba activo."""
        if self.filters.scope == "cerrados":
            scope = self.scope_before_closed
            if scope == "cerrados" or scope not in SCOPES:
                scope = DEFAULT_SCOPE
            return self.set_scope(scope)
        return self.set_scope("cerrados")

    def cycle_filter(self) -> str:
        return self.cycle_scope()

    @property
    def filter_name(self) -> str:
        return self.filters.scope

    @filter_name.setter
    def filter_name(self, value: str) -> None:
        self.set_scope(value)

    def set_query(self, text: str) -> None:
        self.filters.query = text
        self.filters.search_codes = None
        self.cursor = 0
        self.scroll = 0
        self.clamp()

    def set_filter(self, name: str, value: str | None = None) -> str | None:
        if name in SCOPES and value is None:
            return self.set_scope(name)
        if name not in {
            "status",
            "priority",
            "agent",
            "project",
            "tag",
            "kind",
            "order",
            "query",
        }:
            raise ValueError(f"Filtro desconocido: {name}")
        setattr(self.filters, name, value)
        self.cursor = 0
        self.scroll = 0
        self.clamp()
        return value

    def clear_filters(self) -> None:
        self.filters.clear()
        self.cursor = 0
        self.scroll = 0
        self.clamp()

    def cycle_order(self) -> str:
        index = (ORDER_MODES.index(self.filters.order) + 1) % len(ORDER_MODES)
        self.filters.order = ORDER_MODES[index]
        self.cursor = 0
        self.scroll = 0
        return self.filters.order

    # ── Panel de detalle ─────────────────────────────────────────────────
    def toggle_detail(self) -> bool:
        self.show_detail = not self.show_detail
        if not self.show_detail:
            self.focus = "list"
        return self.show_detail

    def toggle_focus(self) -> str:
        if not self.show_detail:
            self.focus = "list"
            return self.focus
        self.focus = "detail" if self.focus == "list" else "list"
        return self.focus

    def toggle_pane(self) -> str:
        try:
            index = PANES.index(self.pane)
        except ValueError:
            index = 0
        self.pane = PANES[(index + 1) % len(PANES)]
        self.detail_scroll = 0
        if self.pane == "heatmap" and self.heatmap_cursor is None:
            self.heatmap_cursor = date.today().isoformat()
        return self.pane

    # ── Mapa de actividad ────────────────────────────────────────────────
    def heatmap_day(self) -> date:
        """Día seleccionado en el mapa (hoy si no hay cursor)."""
        return heatmap.parse_day(self.heatmap_cursor) or date.today()

    def heatmap_first_day(self) -> date:
        """Primer día del calendario que muestra el mapa."""
        today = date.today()
        return today - timedelta(days=today.weekday()) - timedelta(
            weeks=heatmap.DEFAULT_WEEKS - 1
        )

    def heatmap_today(self) -> None:
        """Vuelve el foco a hoy."""
        self.heatmap_cursor = date.today().isoformat()

    def heatmap_move(self, days: int) -> None:
        """Mueve el cursor de día dentro del rango del calendario."""
        target = self.heatmap_day() + timedelta(days=days)
        self.heatmap_cursor = min(
            max(target, self.heatmap_first_day()), date.today()
        ).isoformat()

    def heatmap_cycle_metric(self) -> str:
        """Cicla la métrica del mapa: eventos → creados → completados."""
        self.heatmap_metric = heatmap.next_metric(self.heatmap_metric)
        return self.heatmap_metric

    def heatmap_values(self) -> dict[date, int]:
        """Serie ``{día: valor}`` de la métrica activa."""
        return heatmap.metric_values(self.heatmap, self.heatmap_metric)

    def scroll_panel(self, delta: int, height: int, total: int) -> None:
        limit = max(0, total - height)
        self.detail_scroll = max(0, min(self.detail_scroll + delta, limit))

    # ── Avisos ───────────────────────────────────────────────────────────
    def notify(self, text: str, level: str = "info", ttl: float = 5.0) -> None:
        self.toasts.append(Toast(text=text, level=level, ttl=ttl))
        del self.toasts[:-3]

    def prune_toasts(self, now: float | None = None) -> None:
        now = now or time.monotonic()
        self.toasts = [toast for toast in self.toasts if toast.alive(now)]

    def active_toast(self, now: float | None = None) -> Toast | None:
        self.prune_toasts(now)
        return self.toasts[-1] if self.toasts else None

    # ── Huella ───────────────────────────────────────────────────────────
    def fingerprint(self) -> tuple:
        """Resumen de los datos que afectan al dibujo.

        Sirve para redibujar solo cuando cambia algo real (tickets, sesiones,
        métricas, actividad o si se alcanzó el límite).
        """
        return (
            tuple(
                (
                    ticket["code"],
                    ticket["status"],
                    ticket["priority"],
                    ticket["kind"],
                    ticket.get("project") or "",
                    ticket.get("assigned_to") or "",
                    ticket.get("last_actor") or "",
                    ticket["title"],
                    tuple(ticket.get("tags") or []),
                )
                for ticket in self.tickets
            ),
            tuple(
                sorted(
                    (code, session or "")
                    for code, session in self.session_map.items()
                )
            ),
            tuple(
                sorted(
                    (code, directory or "")
                    for code, directory in self.session_dirs.items()
                )
            ),
            tuple(
                (event.get("code"), event.get("id"), event.get("action"))
                for event in self.activity
            ),
            tuple(sorted((self.stats or {}).get("por_estado", {}).items())),
            self.truncated,
            self.heatmap_metric,
        )
