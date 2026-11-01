"""Mapa de actividad, panel de detalle y dibujo."""

from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path
import tui
from ticketlib import repo
from tui import data as tui_data
from tui import heatmap as tui_heatmap
from tui import view as tui_view
from tests.seeds import seed_tui
from tests.support import Harness


def run(h: Harness) -> None:
    conn = h.conn
    tmp = str(h.tmp)

    fixture = seed_tui(conn)
    t_live = fixture["live"]

    # ── Mapa de actividad: calendario, niveles y composición ───────
    monday = date(2026, 10, 5)
    grid = tui_heatmap.build_grid({monday: 3}, monday, weeks=2)
    h.ok(
        len(grid) == 2
        and grid[0][0].day == date(2026, 9, 28)
        and grid[-1][0].day == monday
        and grid[-1][0].value == 3,
        "el mapa empieza en lunes y coloca el valor en su día",
    )
    h.ok(grid[-1][-1] is None, "el mapa no inventa días futuros")
    h.ok(
        tui_heatmap.cuts([1, 2, 3, 4, 5, 6, 7, 8]) == (3, 5, 7)
        and tui_heatmap.cuts([]) == (1, 2, 3),
        "los cortes usan cuartiles y garantizan monotonía",
    )
    h.ok(
        [tui_heatmap.level(v, (3, 5, 7)) for v in (0, 1, 3, 4, 7, 9)]
        == [0, 1, 1, 2, 3, 4],
        "level reparte los valores en 0..4 según los cortes",
    )
    label_grid = tui_heatmap.build_grid({}, monday, weeks=52)
    month_marks = tui_heatmap.month_labels(label_grid)
    h.ok(
        month_marks
        and month_marks[0][1] == "OCT"
        and all(
            later[0] - earlier[0] >= 4
            for earlier, later in zip(month_marks, month_marks[1:])
        ),
        "las etiquetas de mes no se solapan",
    )
    counts = {
        monday.isoformat(): {"eventos": 5, "creados": 2, "completados": 1},
        (monday - timedelta(days=1)).isoformat(): {
            "eventos": 2,
            "creados": 1,
            "completados": 0,
        },
    }
    view = tui_heatmap.render_map(
        counts, width=40, today=monday, selected=monday
    )
    texts = ["".join(segment.text for segment in line) for line in view.lines]
    h.ok(all(len(text) <= 40 for text in texts), "el mapa respeta el ancho")
    h.ok(
        any("menos" in text and "más" in text for text in texts),
        "el mapa incluye la leyenda de intensidad",
    )
    h.ok(
        any("lunes 5 oct 2026" in text and "5 eventos" in text for text in texts),
        "el pie identifica el día seleccionado",
    )
    h.ok(
        any("creados 2" in text and "completados 1" in text for text in texts),
        "el pie desglosa altas y cierres",
    )
    h.ok(
        view.total == 7 and view.active_days == 2,
        "el mapa suma solo la ventana visible",
    )
    cell = tui_heatmap.day_at(view, monday.weekday(), view.weeks - 1)
    h.ok(
        cell is not None and cell.day == monday,
        "day_at encuentra el día en la rejilla",
    )
    h.ok(
        tui_heatmap.day_at(view, 7, 0) is None
        and tui_heatmap.day_at(view, 0, -1) is None
        and tui_heatmap.day_at(view, 0, view.weeks) is None,
        "day_at ignora coordenadas fuera del mapa",
    )
    empty = tui_heatmap.render_map({}, width=40, today=monday)
    h.ok(
        any(
            "sin actividad" in "".join(seg.text for seg in line)
            for line in empty.lines
        ),
        "el mapa avisa cuando no hay actividad en la ventana",
    )
    h.ok(
        tui_heatmap.next_metric("eventos") == "creados"
        and tui_heatmap.next_metric("completados") == "eventos",
        "el ciclo de métricas vuelve al inicio",
    )
    creados = tui_heatmap.render_map(
        counts, metric="creados", width=40, today=monday, selected=monday
    )
    h.ok(creados.focus.value == 2, "el mapa cambia de métrica")
    wide = tui_heatmap.render_map(counts, width=60, today=monday)
    narrow = tui_heatmap.render_map(counts, width=30, today=monday)
    h.ok(wide.weeks > narrow.weeks, "un panel estrecho muestra menos semanas")
    old_day = monday - timedelta(days=120)
    shifted = tui_heatmap.render_map(
        counts, width=40, today=monday, selected=old_day
    )
    h.ok(
        shifted.start <= old_day <= shifted.end,
        "un día antiguo desplaza la ventana del mapa",
    )

    map_state = tui.TuiState()
    map_state.load(
        [],
        heatmap={monday.isoformat(): {"eventos": 4, "creados": 1}},
    )
    h.ok(
        map_state.heatmap_values() == {monday: 4},
        "el estado expone la serie de la métrica activa",
    )
    map_state.heatmap_cycle_metric()
    h.ok(
        map_state.heatmap_metric == "creados"
        and map_state.heatmap_values() == {monday: 1},
        "el estado cambia de métrica",
    )
    before_print = map_state.fingerprint()
    map_state.heatmap_cycle_metric()
    h.ok(map_state.fingerprint() != before_print, "la métrica entra en la huella")
    map_state.heatmap_move(9999)
    h.ok(
        map_state.heatmap_cursor == date.today().isoformat(),
        "el cursor no pasa de hoy",
    )
    map_state.heatmap_move(-9999)
    h.ok(
        map_state.heatmap_cursor == map_state.heatmap_first_day().isoformat(),
        "el cursor no sale del calendario",
    )

    # ── Mapa de actividad: dibujo real en una ventana falsa ───────
    class FakePalette:
        def __init__(self):
            self.heat_calls = []

        def heat(self, level, selected=False):
            self.heat_calls.append((level, selected))
            return 0

        def attr(self, *args, **kwargs):
            return 0

    class FakeWindow:
        def __init__(self, height, width):
            self.height, self.width = height, width
            self.rows = [[" "] * width for _ in range(height)]

        def addstr(self, y, x, text, attr=0):
            if not 0 <= y < self.height:
                return
            for offset, char in enumerate(text):
                if 0 <= x + offset < self.width:
                    self.rows[y][x + offset] = char

        def text(self):
            return "\n".join("".join(row) for row in self.rows)

    fake_palette = FakePalette()
    h.ok(
        tui_view._segment_attr("heat3", fake_palette) == 0
        and fake_palette.heat_calls[-1] == (3, False),
        "los segmentos heatN usan el nivel del mapa",
    )
    h.ok(
        tui_view._segment_attr("heat1_sel", fake_palette) == 0
        and fake_palette.heat_calls[-1] == (1, True),
        "el día seleccionado pide el nivel resaltado",
    )

    frame_state = tui.TuiState()
    frame_state.load([], heatmap=counts)
    frame_state.pane = "heatmap"
    frame_state.focus = "detail"
    frame_state.heatmap_cursor = monday.isoformat()
    frame_layout = tui_view.geometry(150, 30, frame_state)
    frame = FakeWindow(30, 150)
    tui_view.draw_columns(frame, frame_layout, frame_state)
    tui_view.draw_panel(frame, frame_layout, frame_state, fake_palette)
    frame_lines = frame.text().splitlines()
    h.ok("MAPA" in frame_lines[2], "la cabecera del panel se rotula MAPA")
    h.ok(
        any("menos" in line and "más" in line for line in frame_lines)
        and any("lunes 5 oct 2026" in line for line in frame_lines),
        "el frame dibuja leyenda y pie del mapa",
    )
    h.ok(
        any("j/k día" in line for line in frame_lines),
        "el frame incluye la ayuda de navegación",
    )
    tui_view.draw_segments(
        frame,
        0,
        0,
        12,
        [tui_heatmap.Segment("x" * 40, "heat4")],
        fake_palette,
    )
    h.ok(
        "".join(frame.rows[0]).startswith("x" * 12),
        "draw_segments recorta al ancho disponible",
    )

    # ── Cabecera: ámbito por defecto y urgentes pendientes ────────
    header_state = tui.TuiState()
    header_state.load(
        [
            {
                "id": 1,
                "code": "T-9001",
                "title": "Urgente confirmado",
                "status": "confirmado",
                "priority": "urgente",
                "kind": "otro",
                "project": ".config",
                "assigned_to": "opencode",
                "tags": [],
            },
            {
                "id": 2,
                "code": "T-9002",
                "title": "Urgente cancelado",
                "status": "cancelado",
                "priority": "urgente",
                "kind": "otro",
                "project": ".config",
                "assigned_to": "opencode",
                "tags": [],
            },
            {
                "id": 3,
                "code": "T-9003",
                "title": "Urgente activo",
                "status": "en_progreso",
                "priority": "urgente",
                "kind": "otro",
                "project": ".config",
                "assigned_to": "opencode",
                "tags": [],
            },
            {
                "id": 4,
                "code": "T-9004",
                "title": "Completado por confirmar",
                "status": "completado",
                "priority": "media",
                "kind": "otro",
                "project": ".config",
                "assigned_to": "opencode",
                "tags": [],
            },
        ]
    )
    header_frame = FakeWindow(8, 150)
    tui_view.draw_header(
        header_frame,
        tui_view.geometry(150, 20, header_state),
        header_state,
        2.0,
        fake_palette,
    )
    header_text = header_frame.text()
    h.ok(
        "urgentes 1" in header_text,
        "la cabecera no cuenta urgentes confirmados ni cancelados",
    )
    h.ok(
        "2/4 visibles" in header_text
        and "ámbito: atención" in header_text
        and "2 oculto(s)" in header_text,
        "la cabecera indica el ámbito y cuántos oculta",
    )

    # ── Panel de detalle y actividad ──────────────────────────────
    detail_ticket = repo.get_ticket(conn, t_live["code"])
    lines = tui.detail_lines(detail_ticket, 50, "ses_viva1")
    text = "\n".join(line.text for line in lines)
    h.ok(
        detail_ticket["code"] in lines[0].text
        and "Estado" in text
        and "opencode" in text
        and "sesión viva" in text,
        "el detalle muestra código, estado y agente",
    )
    h.ok(
        "Descripción" in text and "Historial" in text,
        "el detalle incluye descripción e historial",
    )
    h.ok(
        all(len(line.text) <= 50 for line in lines),
        "las líneas del detalle respetan el ancho",
    )
    h.ok(
        tui.detail_lines(None, 40)[0].text.startswith("Sin ticket"),
        "sin ticket el panel lo dice",
    )
    lines_dir = tui.detail_lines(detail_ticket, 50, "ses_viva1", "/home/rowlm")
    text_dir = "\n".join(line.text for line in lines_dir)
    h.ok(
        "Directorio" in text_dir and "/home/rowlm" in text_dir,
        "el detalle muestra el directorio de la sesión",
    )
    lines_gone = tui.detail_lines(
        detail_ticket, 50, "ses_viva1", str(Path(tmp) / "borrado")
    )
    h.ok(
        any("ya no existe" in line.text for line in lines_gone),
        "el detalle avisa si el directorio de la sesión ya no existe",
    )
    h.ok(
        "Directorio"
        not in "\n".join(
            line.text for line in tui.detail_lines(detail_ticket, 50)
        ),
        "sin sesión viva el detalle no muestra directorio",
    )
    dirs_state = tui.TuiState()
    dirs_state.load(
        [detail_ticket],
        {detail_ticket["code"]: "ses_viva1"},
        session_dirs={detail_ticket["code"]: "/home/rowlm"},
    )
    h.ok(
        dirs_state.session_dir_of(detail_ticket) == "/home/rowlm",
        "el estado expone el directorio de la sesión",
    )
    h.ok(
        [line.style for line in lines][:1] == ["title"],
        "la primera línea del detalle es el título",
    )
    activity = tui.activity_lines(
        [
            {
                "ts": "2026-10-31T12:00:00-05:00",
                "code": "T-0001",
                "action": "crear",
                "actor": "opencode",
                "from_status": None,
                "to_status": "nuevo",
                "note": None,
            }
        ],
        40,
    )
    h.ok(
        "T-0001" in activity[0].text and "crear" in activity[0].text,
        "el feed de actividad muestra código y acción",
    )
    h.ok(
        max(len(chunk) for chunk in tui.wrap("x" * 90, 20)) <= 20,
        "wrap respeta el ancho",
    )

    # ── Snapshot y sondeo incremental ─────────────────────────────
    snapshot = tui_data.load_snapshot(conn, limit=2)
    h.ok(
        len(snapshot.tickets) <= 2 and snapshot.truncated,
        "load_snapshot respeta el límite",
    )
    h.ok(
        snapshot.stats["total"] >= len(snapshot.tickets)
        and snapshot.activity,
        "el snapshot trae métricas y actividad",
    )
    h.ok(
        snapshot.stamp == tui_data.db_stamp(conn),
        "el snapshot guarda la huella de la base",
    )
    h.ok(
        snapshot.heatmap and date.today().isoformat() in snapshot.heatmap,
        "el snapshot trae el mapa de actividad",
    )

    # ── Mapa de actividad: consulta diaria y filtros ──────────────
    agg = repo.create_ticket(
        conn, title="Serie diaria", project="agg", created_by="opencode"
    )
    for ts, action, to_status in (
        ("2026-10-01T10:00:00-05:00", "crear", "nuevo"),
        ("2026-10-01T18:30:00-05:00", "transicion", "completado"),
        ("2026-01-15T09:00:00-05:00", "crear", "nuevo"),
    ):
        conn.execute(
            "INSERT INTO events (ticket_id, ts, actor, actor_type, session_id, "
            "action, from_status, to_status, note) "
            "VALUES (?, ?, 'opencode', 'agente', NULL, ?, NULL, ?, NULL)",
            (agg["id"], ts, action, to_status),
        )
    conn.commit()
    serie = repo.activity_by_day(conn, days=371, today="2026-10-31")
    h.ok(
        serie["2026-10-01"]
        == {"eventos": 2, "creados": 1, "completados": 1},
        "activity_by_day agrega eventos, altas y cierres por día",
    )
    h.ok(
        serie["2026-01-15"]["creados"] == 1,
        "activity_by_day incluye días antiguos dentro del rango",
    )
    h.ok(
        "2026-01-15"
        not in repo.activity_by_day(conn, days=40, today="2026-10-31"),
        "activity_by_day respeta el número de días",
    )
    h.ok(
        {"2026-10-01", "2026-01-15"}
        <= set(repo.activity_by_day(conn, project="agg", today="2026-10-31")),
        "activity_by_day filtra por proyecto",
    )
    h.ok(
        repo.activity_by_day(conn, agent="nadie", today="2026-10-31") == {},
        "activity_by_day filtra por agente",
    )
    agg_snapshot = tui_data.load_snapshot(conn, limit=50, project="agg")
    h.ok(
        {"2026-10-01", "2026-01-15"} <= set(agg_snapshot.heatmap),
        "el snapshot pasa el filtro de proyecto al mapa",
    )
    filtered_source = tui_data.DataSource(limit=50)
    h.ok(
        filtered_source.poll(conn, project="agg") is not None
        and filtered_source.poll(conn, project="agg") is None,
        "cambiar el filtro del mapa fuerza una recarga sola una vez",
    )
    h.ok(
        filtered_source.poll(conn, project=None) is not None,
        "quitar el filtro vuelve a cargar el mapa",
    )
    source = tui_data.DataSource(limit=50)
    first = source.poll(conn)
    h.ok(first is not None and first.tickets, "la primera consulta carga datos")
    h.ok(source.poll(conn) is None, "sin cambios no se recarga")
    repo.create_ticket(conn, title="Dispara el sondeo")
    h.ok(
        source.poll(conn) is not None,
        "un ticket nuevo dispara la recarga",
    )
    alive, registered = snapshot.session_stats()
    h.ok(
        registered >= alive >= 0,
        "las métricas de sesión son coherentes",
    )
