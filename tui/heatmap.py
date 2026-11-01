"""Mapa de actividad estilo GitHub, sin curses y fácil de testear.

El módulo separa tres cosas: el calendario (``build_grid``), la escala de
intensidad (``cuts``/``level``) y la composición visual (``render_map``), que
devuelve líneas de segmentos con estilo simbólico. La vista curses solo tiene
que traducir cada estilo a un par de color.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any, Iterable

WEEKDAY_LABELS = ("L", "M", "X", "J", "V", "S", "D")
WEEKDAY_NAMES = (
    "lunes",
    "martes",
    "miércoles",
    "jueves",
    "viernes",
    "sábado",
    "domingo",
)
MONTHS = (
    "ene",
    "feb",
    "mar",
    "abr",
    "may",
    "jun",
    "jul",
    "ago",
    "sep",
    "oct",
    "nov",
    "dic",
)
MONTHS_UPPER = tuple(name.upper() for name in MONTHS)

# Caracteres por nivel: se lee aunque no haya color.
CELL_CHARS = ("·", "░", "▒", "▓", "█")
METRICS = ("eventos", "creados", "completados")
METRIC_LABELS = {
    "eventos": "eventos",
    "creados": "creados",
    "completados": "completados",
}
DEFAULT_WEEKS = 52
# Ancho mínimo para dibujar una semana (celda + separación).
WEEK_WIDTH = 2
GUTTER = 2


@dataclass(frozen=True)
class Cell:
    """Un día del calendario con su valor."""

    day: date
    value: int


@dataclass(frozen=True)
class Segment:
    """Trozo de línea con un estilo simbólico (``heat3``, ``dim``…)."""

    text: str
    style: str = "value"


@dataclass(frozen=True)
class MapView:
    """Resultado de componer el mapa para un ancho y un día seleccionado."""

    lines: list[list[Segment]]
    grid: list[list[Cell | None]]
    limits: tuple[int, int, int]
    metric: str
    focus: Cell
    weeks: int
    window: int
    start: date
    end: date
    total: int
    active_days: int


# Fila (relativa a ``MapView.lines``) donde empieza la rejilla de días.
GRID_TOP = 2


def parse_day(value: Any) -> date | None:
    """Convierte ``2026-10-31`` (o un ``date``) en fecha; ``None`` si no vale."""
    if isinstance(value, date):
        return value
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return date.fromisoformat(text[:10])
    except ValueError:
        return None


def metric_values(
    counts: dict[str, dict[str, int]],
    metric: str = "eventos",
) -> dict[date, int]:
    """Extrae ``{fecha: valor}`` de los contadores diarios del repositorio."""
    name = metric if metric in METRICS else "eventos"
    values: dict[date, int] = {}
    for key, data in (counts or {}).items():
        day = parse_day(key)
        if day is None:
            continue
        values[day] = int((data or {}).get(name) or 0)
    return values


def next_metric(metric: str) -> str:
    """Siguiente métrica del ciclo ``eventos → creados → completados``."""
    try:
        index = METRICS.index(metric)
    except ValueError:
        return METRICS[0]
    return METRICS[(index + 1) % len(METRICS)]


def build_grid(
    values: dict[date, int],
    today: date,
    weeks: int = DEFAULT_WEEKS,
) -> list[list[Cell | None]]:
    """Rejilla de ``weeks`` columnas × 7 filas con el lunes como primera fila.

    Las semanas posteriores a ``today`` se rellenan con ``None`` para no
    inventar días futuros.
    """
    weeks = max(1, int(weeks))
    start = today - timedelta(days=today.weekday()) - timedelta(weeks=weeks - 1)
    grid: list[list[Cell | None]] = []
    for column in range(weeks):
        week: list[Cell | None] = []
        for row in range(7):
            day = start + timedelta(days=column * 7 + row)
            if day > today:
                week.append(None)
            else:
                week.append(Cell(day, int(values.get(day, 0))))
        grid.append(week)
    return grid


def cuts(values: Iterable[int]) -> tuple[int, int, int]:
    """Cortes de los cuatro niveles por cuartiles de los días con actividad.

    Garantiza cortes estrictamente crecientes: sin actividad devuelve
    ``(1, 2, 3)`` y todo queda en el nivel 0.
    """
    positives = sorted(int(value) for value in values if int(value) > 0)
    if not positives:
        return (1, 2, 3)
    total = len(positives)

    def pick(quantile: float) -> int:
        index = min(total - 1, int(total * quantile))
        return positives[index]

    result: list[int] = []
    previous = 0
    for quantile in (0.25, 0.5, 0.75):
        value = max(pick(quantile), previous + 1)
        result.append(value)
        previous = value
    return (result[0], result[1], result[2])


def level(value: int, limits: tuple[int, int, int]) -> int:
    """Nivel 0-4 de un valor según los cortes (0 = sin actividad)."""
    if value <= 0:
        return 0
    for index, limit in enumerate(limits, start=1):
        if value <= limit:
            return index
    return 4


def month_labels(grid: list[list[Cell | None]]) -> list[tuple[int, str]]:
    """Etiquetas de mes ``(columna, "OCT")`` sin solaparse."""
    labels: list[tuple[int, str]] = []
    last_key: tuple[int, int] | None = None
    last_col = -10
    for column, week in enumerate(grid):
        first = next((cell for cell in week if cell is not None), None)
        if first is None:
            continue
        key = (first.day.year, first.day.month)
        if key != last_key and column >= last_col + 4:
            labels.append((column, MONTHS_UPPER[first.day.month - 1]))
            last_key = key
            last_col = column
    return labels


def day_at(view: MapView, row: int, column: int) -> Cell | None:
    """Celda de la rejilla en coordenadas relativas ``(fila, columna)``.

    ``row`` 0-6 empieza en lunes y ``column`` 0 en la primera semana visible.
    Devuelve ``None`` fuera del mapa o en un día futuro.
    """
    if not (0 <= row < 7) or not (0 <= column < view.weeks):
        return None
    index = view.window + column
    if not (0 <= index < len(view.grid)):
        return None
    return view.grid[index][row]


def range_label(start: date, end: date) -> str:
    """``31 oct 2025 → 31 oct 2026`` con meses cortos."""
    return f"{_day_label(start)} → {_day_label(end)}"


def _day_label(day: date) -> str:
    return f"{day.day} {MONTHS[day.month - 1]} {day.year}"


def day_label(day: date) -> str:
    """``vie 31 oct 2026`` para el pie del mapa."""
    return f"{WEEKDAY_NAMES[day.weekday()]} {day.day} {MONTHS[day.month - 1]} {day.year}"


def visible_weeks(width: int) -> int:
    """Columnas de semana que caben en ``width`` (mínimo 1)."""
    usable = max(0, width - GUTTER)
    return max(1, usable // WEEK_WIDTH)


def _window(
    grid: list[list[Cell | None]],
    columns: int,
    focus: date,
    today: date,
) -> tuple[int, int]:
    """Rango ``[inicio, fin)`` de columnas visibles alrededor del foco."""
    total = len(grid)
    if total <= columns:
        return 0, total
    monday = today - timedelta(days=today.weekday())
    column = (focus - monday).days // 7 + total - 1
    column = max(0, min(column, total - 1))
    start = max(0, min(column - columns + 1, total - columns))
    return start, start + columns


def _clip(segments: list[Segment], width: int) -> list[Segment]:
    """Recorta una línea de segmentos al ancho disponible."""
    out: list[Segment] = []
    room = max(0, width)
    for segment in segments:
        if room <= 0:
            break
        text = segment.text[:room]
        if text:
            out.append(Segment(text, segment.style))
        room -= len(text)
    return out


def render_map(
    counts: dict[str, dict[str, int]],
    *,
    metric: str = "eventos",
    width: int = 44,
    today: date | None = None,
    selected: date | None = None,
    weeks: int = DEFAULT_WEEKS,
) -> MapView:
    """Compone la vista completa del mapa para un ancho dado."""
    today = today or date.today()
    name = metric if metric in METRICS else METRICS[0]
    values = metric_values(counts, name)
    grid = build_grid(values, today, weeks)
    limits = cuts(key.value for week in grid for key in week if key is not None)
    focus_day = selected or today
    focus_day = min(max(focus_day, grid[0][0].day if grid[0][0] else today), today)
    column_count = visible_weeks(width)
    start, end = _window(grid, column_count, focus_day, today)
    visible = grid[start:end]

    visible_values = [
        cell.value
        for week in visible
        for cell in week
        if cell is not None
    ]
    total = sum(visible_values)
    active_days = sum(1 for value in visible_values if value > 0)
    lines: list[list[Segment]] = []
    first_day = next((cell.day for week in visible for cell in week if cell), today)
    last_day = next(
        (cell.day for week in reversed(visible) for cell in reversed(week) if cell),
        today,
    )

    header = [
        Segment(f"{METRIC_LABELS[name]} ", "label"),
        Segment(f"{total}", "accent"),
    ]
    suffix = f" · {active_days} de {len(visible_values)} días con actividad"
    if total == 0:
        suffix = " · sin actividad en la ventana"
    if sum(len(part.text) for part in header) + len(suffix) <= width:
        header.append(Segment(suffix, "dim"))
    lines.append(_clip(header, width))

    month_row = [Segment(" " * GUTTER)]
    labels = {
        column - start: text
        for column, text in month_labels(grid)
        if start <= column < end
    }
    if 0 not in labels and visible:
        first_cell = next((cell for week in visible for cell in week if cell), None)
        if first_cell is not None:
            labels[0] = MONTHS_UPPER[first_cell.day.month - 1]
    # Las etiquetas ocupan 3 caracteres y las semanas 2: se pinta sobre un
    # búfer para que "JUN"/"JUL" no se recorten a "JU". Si dos etiquetas
    # quedarían pegadas se omite la segunda.
    buffer = [" "] * (len(visible) * WEEK_WIDTH)
    cursor = -3
    for index in sorted(labels):
        position = index * WEEK_WIDTH
        if position < cursor + 3:
            continue
        for offset, char in enumerate(labels[index][:3]):
            if position + offset < len(buffer):
                buffer[position + offset] = char
        cursor = position
    for index in range(0, len(buffer), WEEK_WIDTH):
        month_row.append(Segment("".join(buffer[index : index + WEEK_WIDTH]), "dim"))
    lines.append(_clip(month_row, width))

    for row in range(7):
        segments: list[Segment] = []
        gutter = WEEKDAY_LABELS[row] if row in (0, 2, 4, 6) else " "
        segments.append(Segment(gutter + " ", "dim"))
        for week in visible:
            cell = week[row]
            if cell is None:
                segments.append(Segment(" " * WEEK_WIDTH, "dim"))
                continue
            number = level(cell.value, limits)
            chosen = cell.day == focus_day
            char = CELL_CHARS[number]
            style = f"heat{number}" + ("_sel" if chosen else "")
            segments.append(Segment(char + " ", style))
        lines.append(_clip(segments, width))

    legend: list[Segment] = [Segment("  "), Segment("menos ", "dim")]
    for number in range(5):
        legend.append(Segment(CELL_CHARS[number] + " ", f"heat{number}"))
    legend.append(Segment("más", "dim"))
    lines.append([Segment("")])
    lines.append(_clip(legend, width))

    focus_cell = next(
        (cell for week in grid for cell in week if cell is not None and cell.day == focus_day),
        None,
    )
    focus_value = focus_cell.value if focus_cell else 0
    lines.append(
        _clip(
            [
                Segment(day_label(focus_day), "label"),
                Segment(f" · {focus_value} {METRIC_LABELS[name]}", "accent" if focus_value else "dim"),
            ],
            width,
        )
    )
    breakdown = counts.get(focus_day.isoformat(), {}) or {}
    lines.append(
        _clip(
            [
                Segment(
                    f"creados {int(breakdown.get('creados') or 0)} · "
                    f"completados {int(breakdown.get('completados') or 0)}",
                    "dim",
                )
            ],
            width,
        )
    )
    lines.append(
        _clip(
            [
                Segment(range_label(first_day, last_day), "dim"),
            ],
            width,
        )
    )
    if width >= 34:
        lines.append(
            _clip(
                [
                    Segment("j/k día · h/l semanas · t métrica", "dim"),
                ],
                width,
            )
        )

    return MapView(
        lines=lines,
        grid=grid,
        limits=limits,
        metric=name,
        focus=Cell(focus_day, focus_value),
        weeks=len(visible),
        window=start,
        start=first_day,
        end=last_day,
        total=total,
        active_days=active_days,
    )
