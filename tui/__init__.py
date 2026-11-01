"""TUI de tickets: ``ticket tui``.

- ``state.py``      — estado, filtros y geometría, sin curses (testeable).
- ``detail.py``     — contenido del panel de detalle y de actividad.
- ``heatmap.py``    — calendario, cuartiles y composición del mapa de actividad.
- ``data.py``       — sondeo incremental y snapshots.
- ``actions.py``    — operaciones de administración sin curses.
- ``actions_ui.py`` — flujos interactivos (confirmar, acciones, sesiones).
- ``view.py``       — dibujo, paleta, menús y modales.
- ``app.py``        — bucle principal, atajos y ratón.
"""

from __future__ import annotations

from .app import run
from .data import DataSource, Snapshot, load_snapshot
from .detail import Line, activity_lines, detail_lines, wrap
from .state import (
    COLUMNS,
    COMPACT_COLUMNS,
    DEFAULT_SCOPE,
    ORDER_MODES,
    PANES,
    SCOPE_LABELS,
    SCOPES,
    Filters,
    TuiState,
    column_spans,
    display_code,
    layout_width,
    muted_style,
    table_width,
)

__all__ = [
    "COLUMNS",
    "COMPACT_COLUMNS",
    "DEFAULT_SCOPE",
    "DataSource",
    "Filters",
    "Line",
    "ORDER_MODES",
    "PANES",
    "SCOPE_LABELS",
    "SCOPES",
    "Snapshot",
    "TuiState",
    "activity_lines",
    "column_spans",
    "detail_lines",
    "display_code",
    "layout_width",
    "load_snapshot",
    "muted_style",
    "run",
    "table_width",
    "wrap",
]
