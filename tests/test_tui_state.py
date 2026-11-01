"""Estado de la TUI, filtros, novedades, mapa y FTS."""

from __future__ import annotations

import time
import tui
from ticketlib import domain, repo, sessions
from tui import data as tui_data
from tui import detail as tui_detail
from tests.seeds import seed_tui
from tests.support import Harness


def run(h: Harness) -> None:
    conn = h.conn
    tmp = str(h.tmp)

    fixture = seed_tui(conn)
    t_live = fixture["live"]
    t_pending = fixture["pending"]

    # ── Estado de la TUI (sin curses) ──────────────────────────────
    state = tui.TuiState()
    state.load(
        repo.list_tickets(conn, limit=None),
        {t_live["code"]: "ses_viva1"},
    )
    h.ok(
        state.visible() and all(
            ticket["status"] in domain.STATUSES
            for ticket in state.visible()
        ),
        "la TUI parte del filtro 'todos'",
    )
    h.ok(
        state.set_scope("completados") == "completados"
        and state.visible()
        and all(
            ticket["status"] == "completado"
            for ticket in state.visible()
        ),
        "el ámbito 'completados' solo muestra completados",
    )
    state.set_scope("abiertos")
    h.ok(
        state.visible()
        and all(
            ticket["status"] in domain.OPEN_STATUSES
            for ticket in state.visible()
        ),
        "el ámbito 'abiertos' excluye estados finales",
    )
    state.set_scope("todos")
    state.set_query("zzz-no-existe")
    h.ok(state.visible() == [], "la búsqueda filtra por texto")
    state.set_query("")

    pending_row = next(
        t for t in state.tickets if t["code"] == t_pending["code"]
    )
    state.cursor = state.visible().index(pending_row)
    h.ok(
        state.toggle() is False,
        "no se pueden marcar tickets que no estén completados",
    )
    marked = state.select_all()
    h.ok(marked >= 1, "select_all marca los completados")
    h.ok(
        all(code in state.selected for code in state.selected_codes()),
        "selected_codes solo devuelve códigos marcados",
    )
    state.clear_selection()
    h.ok(state.selected == set(), "clear_selection vacía la selección")

    state.cursor = 0
    state.scroll = 0
    state.ensure_visible(0)
    h.ok(state.scroll == 0, "ensure_visible tolera altura cero")
    state.move(len(state.tickets) * 2)
    state.ensure_visible(3)
    h.ok(
        state.cursor >= state.scroll
        and state.cursor < state.scroll + 3,
        "el cursor queda dentro de la ventana visible",
    )
    h.ok(
        tui.layout_width() >= 60,
        "la TUI define un ancho mínimo razonable",
    )

    # ── Auto-refresh: huella, cursor y estado ─────────────────────
    live_state = tui.TuiState()
    live_state.load(
        repo.list_tickets(conn, limit=None),
        {t_live["code"]: "ses_viva1"},
        truncated=False,
    )
    h.ok(live_state.auto_refresh, "el auto-refresh viene activado")
    h.ok(
        live_state.truncated is False,
        "load guarda el indicador de límite alcanzado",
    )
    before_print = live_state.fingerprint()
    live_state.load(
        repo.list_tickets(conn, limit=None),
        {t_live["code"]: "ses_viva1"},
        truncated=False,
    )
    h.ok(
        live_state.fingerprint() == before_print,
        "la huella no cambia si los datos son iguales",
    )
    live_state.load(
        repo.list_tickets(conn, limit=None),
        {t_live["code"]: "ses_viva2"},
        truncated=False,
    )
    h.ok(
        live_state.fingerprint() != before_print,
        "la huella cambia cuando cambia la sesión",
    )
    live_state.load(
        repo.list_tickets(conn, limit=None),
        {t_live["code"]: "ses_viva1"},
        truncated=True,
    )
    h.ok(
        live_state.fingerprint() != before_print,
        "la huella cambia al alcanzar el límite",
    )

    focus_target = live_state.visible()[3]
    live_state.cursor = 3
    fresh = repo.list_tickets(conn, limit=None)
    repo.create_ticket(conn, title="Creado mientras la TUI está abierta")
    live_state.load(
        repo.list_tickets(conn, limit=None),
        {t_live["code"]: "ses_viva1"},
        keep_code=focus_target["code"],
    )
    h.ok(
        live_state.current()["code"] == focus_target["code"],
        "load conserva el cursor sobre el mismo código",
    )
    h.ok(
        len(live_state.tickets) == len(fresh) + 1,
        "un ticket nuevo aparece al recargar",
    )
    h.ok(
        live_state.focus_on("T-9999") is False,
        "focus_on ignora códigos que ya no están visibles",
    )

    # ── Filtros combinables, orden y búsqueda ─────────────────────
    filters_state = tui.TuiState()
    filters_state.load(repo.list_tickets(conn, limit=None))
    alta = next(
        t
        for t in filters_state.tickets
        if t["priority"] == "alta" and t.get("project")
    )
    filters_state.set_filter("priority", "alta")
    h.ok(
        filters_state.visible()
        and all(
            t["priority"] == "alta" for t in filters_state.visible()
        ),
        "el filtro por prioridad solo deja esa prioridad",
    )
    filters_state.set_filter("agent", sessions.agent_name(alta))
    h.ok(
        filters_state.visible()
        and all(
            sessions.agent_name(t) == sessions.agent_name(alta)
            for t in filters_state.visible()
        ),
        "el filtro por agente usa el agente efectivo",
    )
    filters_state.set_filter("priority", None)
    filters_state.set_filter("agent", None)
    filters_state.set_filter("project", alta["project"])
    h.ok(
        all(
            t["project"] == alta["project"]
            for t in filters_state.visible()
        ),
        "el filtro por proyecto funciona",
    )
    filters_state.clear_filters()
    h.ok(
        len(filters_state.matches()) == len(filters_state.tickets),
        "clear_filters devuelve la lista completa",
    )

    tagged = repo.create_ticket(
        conn, title="Con etiqueta única", tags=["aguja-ui"]
    )
    repo.update_ticket(
        conn, tagged["code"], body="cuerpo con aguja-secreta"
    )
    filters_state.load(repo.list_tickets(conn, limit=None))
    filters_state.set_filter("tag", "aguja-ui")
    h.ok(
        [t["code"] for t in filters_state.visible()] == [tagged["code"]],
        "el filtro por etiqueta encuentra el ticket",
    )
    filters_state.clear_filters()
    filters_state.set_query("aguja-secreta")
    h.ok(
        [t["code"] for t in filters_state.visible()] == [tagged["code"]],
        "la búsqueda mira también el cuerpo",
    )
    filters_state.set_query("aguja-ui")
    h.ok(
        [t["code"] for t in filters_state.visible()] == [tagged["code"]],
        "la búsqueda mira también las etiquetas",
    )
    filters_state.set_query("")
    filters_state.set_filter("priority", "alta")
    filters_state.set_filter("kind", "funcionalidad")
    h.ok(
        all(
            t["priority"] == "alta" and t["kind"] == "funcionalidad"
            for t in filters_state.visible()
        ),
        "los filtros se combinan",
    )
    filters_state.clear_filters()
    h.ok(
        filters_state.cycle_order() in tui.ORDER_MODES,
        "cycle_order alterna entre código y prioridad",
    )
    h.ok(
        filters_state.filters.order == tui.ORDER_MODES[1],
        "el orden cambia de verdad",
    )
    h.ok(
        [name for name, _ in filters_state.filters.active()] == [],
        "sin filtros no hay etiquetas activas",
    )

    # ── Toasts, novedades y paginación ────────────────────────────
    notices = tui.TuiState()
    notices.notify("uno")
    notices.notify("dos", "ok")
    notices.notify("tres", "error")
    notices.notify("cuatro")
    h.ok(len(notices.toasts) == 3, "solo se conservan los 3 últimos avisos")
    h.ok(
        notices.active_toast().text == "cuatro",
        "el aviso más reciente es el visible",
    )
    later = time.monotonic() + 60
    h.ok(
        notices.active_toast(now=later) is None,
        "los avisos caducan",
    )

    fresh_state = tui.TuiState()
    fresh_state.load(repo.list_tickets(conn, limit=None))
    h.ok(fresh_state.fresh == {}, "la primera carga no marca novedades")
    target_code = fresh_state.tickets[0]["code"]
    repo.update_ticket(
        conn, target_code, title="Título cambiado para la novedad"
    )
    fresh_state.load(repo.list_tickets(conn, limit=None))
    h.ok(
        fresh_state.is_fresh(target_code),
        "un ticket cambiado se marca como novedad",
    )
    h.ok(
        fresh_state.is_fresh(
            target_code, now=time.monotonic() + 60
        )
        is False,
        "la marca de novedad caduca",
    )

    panel = tui.TuiState()
    panel.scroll_panel(3, 10, 30)
    h.ok(panel.detail_scroll == 3, "scroll_panel mueve el panel")
    panel.scroll_panel(100, 10, 30)
    h.ok(panel.detail_scroll == 20, "scroll_panel no pasa del final")
    panel.scroll_panel(-100, 10, 30)
    h.ok(panel.detail_scroll == 0, "scroll_panel no pasa del inicio")
    h.ok(
        panel.toggle_focus() == "detail"
        and panel.toggle_focus() == "list",
        "tab alterna el foco",
    )
    h.ok(
        panel.toggle_detail() is False
        and panel.focus == "list"
        and panel.toggle_detail() is True,
        "d oculta y muestra el panel",
    )
    h.ok(
        panel.toggle_pane() == "activity"
        and panel.toggle_pane() == "heatmap"
        and panel.toggle_pane() == "detail",
        "v cicla detalle, actividad y mapa",
    )

    page = tui.TuiState(limit=1)
    page.load(repo.list_tickets(conn, limit=1), truncated=True)
    page.cursor = len(page.visible()) - 1
    h.ok(page.needs_more(), "needs_more detecta que quedan páginas")
    page.truncated = False
    h.ok(not page.needs_more(), "sin truncado no se piden más páginas")

    # ── Búsqueda FTS, comentarios y resaltado ─────────────────────
    needle = repo.create_ticket(
        conn, title="Paneles de control", body="cuerpo sin pista"
    )
    repo.comment_ticket(
        conn,
        needle["code"],
        "aqui va la aguja dorada",
        author="codex",
    )
    h.ok(
        needle["code"] in repo.search_codes(conn, "paneles"),
        "search_codes usa FTS para el título",
    )
    h.ok(
        needle["code"] in repo.search_codes(conn, "aguja"),
        "search_codes encuentra coincidencias en comentarios",
    )
    h.ok(
        needle["code"] in repo.search_codes(conn, "aguja dorada"),
        "search_codes exige todos los tokens",
    )
    h.ok(
        needle["code"] not in repo.search_codes(conn, "aguja plateada"),
        "search_codes no mezcla tokens de más",
    )
    h.ok(
        repo.search_codes(conn, "zzz-sin-coincidencias") == set(),
        "sin coincidencias no devuelve códigos",
    )
    h.ok(repo.search_codes(conn, "   ") == set(), "una búsqueda vacía no consulta")

    h.ok(
        tui_detail.highlight_spans("Hola mundo hola", ["hola"])
        == [(0, 4), (11, 15)],
        "highlight_spans encuentra todas las apariciones",
    )
    h.ok(
        tui_detail.highlight_spans("Águja y AGUJA", ["aguja"])
        == [(0, 5), (8, 13)],
        "el resaltado ignora mayúsculas y diacríticos",
    )
    h.ok(
        tui_detail.highlight_spans("sin coincidencia", ["aguja"]) == [],
        "sin coincidencia no hay rangos",
    )
    h.ok(
        tui_detail.highlight_spans("abc", ["abc", "bc"]) == [(0, 3)],
        "los rangos solapados se fusionan",
    )

    filters = tui.Filters(query="aguja", search_codes={needle["code"]})
    h.ok(
        filters.matches(
            {
                "code": needle["code"],
                "title": "Paneles de control",
                "status": "nuevo",
                "priority": "media",
                "kind": "otro",
                "project": None,
                "assigned_to": None,
                "tags": [],
            }
        ),
        "un ticket que solo coincide por comentario pasa el filtro",
    )
    h.ok(
        not filters.matches(
            {
                "code": "T-9999",
                "title": "Otro",
                "status": "nuevo",
                "priority": "media",
                "kind": "otro",
                "project": None,
                "assigned_to": None,
                "tags": [],
            }
        ),
        "un código fuera de search_codes no pasa el filtro",
    )
    search_snapshot = tui_data.load_snapshot(conn, limit=50, query="aguja")
    h.ok(
        search_snapshot.search_codes
        and needle["code"] in search_snapshot.search_codes,
        "el snapshot incluye los códigos de la búsqueda",
    )
    h.ok(
        tui_data.load_snapshot(conn, limit=50).search_codes is None,
        "sin búsqueda el snapshot no calcula códigos",
    )
    search_source = tui_data.DataSource(limit=50)
    search_source.poll(conn)
    h.ok(
        search_source.poll(conn, query="aguja") is not None,
        "cambiar la búsqueda fuerza una recarga",
    )
    h.ok(
        search_source.poll(conn, query="aguja") is None,
        "repetir la misma búsqueda no recarga",
    )

    search_state = tui.TuiState()
    search_state.set_query("aguja")
    search_state.load(
        repo.list_tickets(conn, limit=None),
        search_codes={needle["code"]},
    )
    h.ok(
        needle["code"]
        in [ticket["code"] for ticket in search_state.visible()],
        "la TUI muestra el ticket que coincide solo por comentario",
    )
    plain_state = tui.TuiState()
    plain_state.set_query("aguja")
    plain_state.load(repo.list_tickets(conn, limit=None))
    h.ok(
        needle["code"]
        not in [ticket["code"] for ticket in plain_state.visible()],
        "sin el conjunto FTS el comentario no aparecería",
    )

    # ── Ámbito por defecto (atención) y contador de urgentes ──────
    urgent_open = repo.create_ticket(
        conn, title="Urgente activo", priority="urgente"
    )
    urgent_confirmed = repo.create_ticket(
        conn, title="Urgente confirmado", priority="urgente"
    )
    repo.claim_ticket(conn, urgent_confirmed["code"], agent="opencode")
    repo.complete_ticket(
        conn, urgent_confirmed["code"], resolution="Hecho"
    )
    repo.confirm_tickets(
        conn, [urgent_confirmed["code"]], actor="usuario"
    )
    urgent_cancelled = repo.create_ticket(
        conn, title="Urgente cancelado", priority="urgente"
    )
    repo.transition(
        conn, urgent_cancelled["code"], "cancelado", actor="test"
    )

    scope_state = tui.TuiState()
    scope_state.load(repo.list_tickets(conn, limit=None))
    scope_stats = repo.stats(conn)
    h.ok(
        scope_stats["urgentes_pendientes"] == 1,
        "stats solo cuenta urgentes que requieren atención",
    )
    scope_codes = [ticket["code"] for ticket in scope_state.matches()]
    h.ok(
        tui.DEFAULT_SCOPE == "atencion"
        and scope_state.filters.scope == "atencion",
        "la TUI arranca en el ámbito 'atención'",
    )
    h.ok(
        urgent_open["code"] in scope_codes
        and urgent_confirmed["code"] not in scope_codes
        and urgent_cancelled["code"] not in scope_codes,
        "atención oculta confirmados y cancelados",
    )
    h.ok(
        fixture["completed"][0]["code"] in scope_codes,
        "atención mantiene los completados por confirmar",
    )
    visible_codes = [ticket["code"] for ticket in scope_state.visible()]
    h.ok(
        urgent_confirmed["code"] not in visible_codes
        and urgent_cancelled["code"] not in visible_codes
        and fixture["completed"][0]["code"] in visible_codes,
        "la lista por defecto aplica el ámbito sin romper el árbol",
    )
    closed = [
        ticket
        for ticket in scope_state.tickets
        if ticket["status"] in domain.FINAL_STATUSES
    ]
    h.ok(
        scope_state.scope_hidden() == len(closed) >= 2,
        "la barra cuenta los ocultos por el ámbito",
    )
    h.ok(
        scope_state.urgent_pending() == 1,
        "la cabecera solo cuenta urgentes que requieren atención",
    )
    h.ok(
        scope_state.cycle_scope() == "todos"
        and scope_state.cycle_scope() == "completados"
        and scope_state.cycle_scope() == "abiertos"
        and scope_state.cycle_scope() == "cerrados"
        and scope_state.cycle_scope() == "atencion",
        "el ciclo de ámbitos vuelve a atención pasando por todos y cerrados",
    )
    scope_state.set_scope("cerrados")
    closed_visible = [ticket["code"] for ticket in scope_state.visible()]
    h.ok(
        scope_state.visible()
        and all(
            ticket["status"] in domain.FINAL_STATUSES
            for ticket in scope_state.visible()
        )
        and urgent_confirmed["code"] in closed_visible
        and urgent_cancelled["code"] in closed_visible,
        "el ámbito 'cerrados' solo muestra confirmados y cancelados",
    )
    h.ok(
        scope_state.toggle_closed_scope() == "atencion"
        and scope_state.toggle_closed_scope() == "cerrados",
        "M alterna cerrados con el ámbito anterior",
    )
    scope_state.set_scope("todos")
    h.ok(
        scope_state.toggle_closed_scope() == "cerrados"
        and scope_state.toggle_closed_scope() == "todos",
        "M vuelve a 'todos' si desde ahí se abrió cerrados",
    )
    scope_state.set_scope("todos")
    h.ok(
        urgent_confirmed["code"]
        in [ticket["code"] for ticket in scope_state.matches()],
        "el ámbito 'todos' vuelve a mostrar los cerrados",
    )

    confirm_only = tui.app._initial_state(["confirmado"], None, None, 50)
    confirm_only.load(repo.list_tickets(conn, limit=None))
    h.ok(
        confirm_only.filters.scope == "todos"
        and confirm_only.visible()
        and all(
            ticket["status"] == "confirmado"
            for ticket in confirm_only.visible()
        ),
        "`--status confirmado` se ve aunque el ámbito oculte los cerrados",
    )

    open_parent = repo.create_ticket(
        conn, title="Épica activa con tareas cerradas"
    )
    closed_child = repo.create_ticket(
        conn, title="Tarea confirmada", parent_id=open_parent["code"]
    )
    repo.claim_ticket(conn, closed_child["code"], agent="opencode")
    repo.complete_ticket(
        conn, closed_child["code"], resolution="Hecho"
    )
    repo.confirm_tickets(conn, [closed_child["code"]], actor="usuario")
    cancelled_child = repo.create_ticket(
        conn, title="Tarea cancelada", parent_id=open_parent["code"]
    )
    repo.transition(
        conn, cancelled_child["code"], "cancelado", actor="test"
    )
    closed_parent = repo.create_ticket(conn, title="Épica ya cerrada")
    repo.claim_ticket(conn, closed_parent["code"], agent="opencode")
    repo.complete_ticket(conn, closed_parent["code"], resolution="Hecho")
    repo.confirm_tickets(conn, [closed_parent["code"]], actor="usuario")
    hidden_child = repo.create_ticket(
        conn, title="Tarea de épica cerrada", parent_id=closed_parent["code"]
    )
    repo.claim_ticket(conn, hidden_child["code"], agent="opencode")
    repo.complete_ticket(conn, hidden_child["code"], resolution="Hecho")
    repo.confirm_tickets(conn, [hidden_child["code"]], actor="usuario")

    tree_state = tui.TuiState()
    tree_state.load(repo.list_tickets(conn, limit=None))
    tree_codes = [ticket["code"] for ticket in tree_state.matches()]
    h.ok(
        closed_child["code"] in tree_codes
        and cancelled_child["code"] in tree_codes
        and hidden_child["code"] not in tree_codes,
        "atención muestra hijos cerrados si el padre sigue activo",
    )
    tree_state.expanded.add(open_parent["code"])
    tree_visible = [ticket["code"] for ticket in tree_state.visible()]
    h.ok(
        closed_child["code"] in tree_visible
        and cancelled_child["code"] in tree_visible,
        "los hijos cerrados se integran en el árbol del padre activo",
    )
    hidden_final = [
        ticket
        for ticket in tree_state.tickets
        if ticket["status"] in domain.FINAL_STATUSES
        and ticket["code"]
        not in (closed_child["code"], cancelled_child["code"])
    ]
    h.ok(
        tree_state.scope_hidden() == len(hidden_final) == 4,
        "los hijos mostrados no cuentan como ocultos por el ámbito",
    )
    tree_state.set_query("Tarea confirmada")
    h.ok(
        [ticket["code"] for ticket in tree_state.matches()]
        == [closed_child["code"]],
        "los filtros manuales siguen aplicándose al hijo cerrado",
    )

    hidden_sel = tui.TuiState()
    hidden_sel.load(repo.list_tickets(conn, limit=None))
    hidden_sel.selected.add(fixture["completed"][0]["code"])
    hidden_sel.set_scope("abiertos")
    h.ok(
        hidden_sel.selected_codes() == [],
        "la selección oculta no se confirma al cambiar de ámbito",
    )
