"""Pantalla de memoria de la TUI: lógica pura, filtros y acciones.

No se prueba curses: la pantalla delega en funciones puras y en
``ticketlib.memory``. Las acciones de aprobación y rechazo son del usuario y
archivar nunca borra.
"""

from __future__ import annotations

from ticketlib import db
from ticketlib.domain import ValidationError
from ticketlib.memory import add_memory, create_playbook, get_memory
from ticketlib.memory.store import list_memories
from tui import memory as tui_memory
from tests.support import Harness


def _ok(h: Harness, *condiciones: tuple[bool, str]) -> None:
    """Agrupa aserciones en un check: todas se evalúan y se conserva el motivo."""
    fallos = [mensaje for cumple, mensaje in condiciones if not cumple]
    h.ok(not fallos, " / ".join(fallos))


def _falla(func, exc) -> bool:
    """True si la llamada lanza la excepción esperada."""
    try:
        func()
    except exc:
        return True
    return False


def _seed(conn) -> dict[str, str]:
    active = add_memory(
        conn,
        title="Convención activa",
        content="Los docs viven en docs/.",
        kind="convention",
        key="tui-active",
        source="user",
    )
    proposal = add_memory(
        conn,
        title="Evitar el SQL duplicado",
        content="Usa el store de memoria, no SQL propio.",
        kind="pitfall",
        key="tui-proposal",
        source="agent",
        evidence=[{"kind": "ticket", "ref": "T-0055"}],
    )
    stale = add_memory(
        conn,
        title="Hecho caducado",
        content="Este hecho ya no aplica.",
        kind="fact",
        key="tui-stale",
        source="user",
    )
    conn.execute(
        "UPDATE memories SET status = 'stale' WHERE code = ?", (stale["code"],)
    )
    archived = add_memory(
        conn,
        title="Memoria archivada",
        content="Se conserva por histórico.",
        kind="fact",
        key="tui-archived",
        source="user",
    )
    conn.execute(
        "UPDATE memories SET status = 'archived', archived_at = ? WHERE code = ?",
        ("2026-01-01T00:00:00-05:00", archived["code"]),
    )
    playbook = create_playbook(
        conn,
        slug="revisar-tui",
        title="Revisar la TUI",
        description="Checklist para revisar pantallas.",
        content="1. Lee el diff.\n2. Ejecuta los gates.",
        evidence=[{"kind": "ticket", "ref": "T-0055"}],
        files=[
            {
                "path": "references/checklist.md",
                "content": "# Checklist\n- Sin curses en tests\n",
            }
        ],
        source="user",
    )
    return {
        "active": active["code"],
        "proposal": proposal["code"],
        "stale": stale["code"],
        "archived": archived["code"],
        "playbook": playbook["code"],
    }


def _by_code(data: dict) -> dict[str, dict]:
    return {memory["code"]: memory for memory in data["memories"]}


def _load(h: Harness) -> tuple[dict, dict[str, str]]:
    codes = _seed(h.conn)
    return tui_memory.load_memories(h.conn), codes


def run(h: Harness) -> None:
    conn = h.conn
    data, codes = _load(h)

    # ── Carga e inventario ─────────────────────────────────────────
    _ok(
        h,
        (data["enabled"], "la memoria habilitada se carga"),
        (
            len(data["memories"]) == 5,
            "carga memorias y playbooks, incluidas inactivas",
        ),
        (
            [item["code"] for item in data["playbooks"]]
            == [codes["playbook"]],
            "separa los playbooks del resto",
        ),
    )
    stats = data["stats"]
    _ok(
        h,
        (stats["total"] == 5, "la foto de stats cuenta todas las memorias"),
        (stats["by_status"]["proposed"] == 1, "stats cuenta propuestas"),
        (stats["by_status"]["stale"] == 1, "stats cuenta stale"),
        (stats["by_status"]["archived"] == 1, "stats cuenta archivadas"),
    )

    # ── Filtros ───────────────────────────────────────────────────
    def codes_of(mode: str) -> set[str]:
        return {
            item["code"]
            for item in tui_memory.filter_memories(data["memories"], mode)
        }

    _ok(
        h,
        (codes_of("propuestas") == {codes["proposal"]}, "filtro propuestas"),
        (
            codes_of("activas") == {codes["active"], codes["playbook"]},
            "filtro activas",
        ),
        (codes_of("stale") == {codes["stale"]}, "filtro stale"),
        (codes_of("playbooks") == {codes["playbook"]}, "filtro playbooks"),
        (len(codes_of("todas")) == 5, "filtro todas incluye archivadas"),
        (
            _falla(
                lambda: tui_memory.filter_memories(
                    data["memories"], "desconocido"
                ),
                ValueError,
            ),
            "un filtro desconocido falla",
        ),
    )

    # ── Orden estable por estado, tipo y código ───────────────────
    ordered = tui_memory.sort_memories(data["memories"])
    statuses = [
        tui_memory.STATUS_ORDER[item["status"]] for item in ordered
    ]
    _ok(
        h,
        (
            ordered[0]["code"] == codes["proposal"],
            "las propuestas van primero",
        ),
        (
            ordered[-1]["code"] == codes["archived"],
            "las archivadas van al final",
        ),
        (statuses == sorted(statuses), "el orden respeta el estado"),
    )

    # ── Cabecera y presupuesto ────────────────────────────────────
    header = tui_memory.header_lines(data)
    _ok(
        h,
        (len(header) == 2, "la cabecera tiene dos líneas"),
        ("propuestas 1" in header[0], "la cabecera cuenta propuestas"),
        ("playbooks 1" in header[0], "la cabecera cuenta playbooks"),
        ("activas 2" in header[0], "la cabecera cuenta activas y playbook"),
        ("5 totales" in header[0], "la cabecera muestra el total"),
    )
    budget = tui_memory.budget_lines(data)
    project = data["stats"]["budget"]["project"]
    _ok(
        h,
        (
            len(budget) == 3,
            "el presupuesto muestra proyecto, usuario y frescura",
        ),
        (
            "Presupuesto proyecto:" in budget[0],
            "el presupuesto usa el scope proyecto",
        ),
        (
            "Presupuesto usuario:" in budget[1],
            "el presupuesto usa el scope usuario",
        ),
        ("frescura stale" in budget[2], "la cabecera muestra frescura"),
        (
            project["used"] > 0,
            "el presupuesto cuenta caracteres usados",
        ),
        (
            project["limit"] == data["config"]["project_budget_chars"],
            "el presupuesto usa el límite configurado",
        ),
    )

    # ── Filas y detalle ───────────────────────────────────────────
    active = _by_code(data)[codes["active"]]
    row = tui_memory.row_text(active, 80)
    _ok(
        h,
        (
            codes["active"] in row and "convention" in row,
            "la fila lleva código y tipo",
        ),
        ("Convención activa" in row, "la fila lleva el título"),
        (
            len(tui_memory.row_text(active, 20)) <= 20,
            "la fila respeta el ancho",
        ),
    )

    get_memory(conn, codes["active"], record_usage=True)
    detail = "\n".join(tui_memory.detail_lines(conn, active, width=60))
    _ok(
        h,
        (codes["active"] in detail, "el detalle muestra el código"),
        ("Convención activa" in detail, "el detalle muestra el título"),
        (
            "Los docs viven en docs/." in detail,
            "el detalle muestra el contenido",
        ),
    )
    _ok(
        h,
        ("uso: 1" in detail, "el detalle muestra el uso registrado"),
        (
            "Presupuesto project:" in detail,
            "el detalle muestra el presupuesto",
        ),
    )

    proposal_detail = "\n".join(
        tui_memory.detail_lines(
            conn, _by_code(data)[codes["proposal"]], width=60
        )
    )
    _ok(
        h,
        ("ticket:T-0055" in proposal_detail, "el detalle lista la evidencia"),
    )

    playbook_detail = "\n".join(
        tui_memory.detail_lines(
            conn, _by_code(data)[codes["playbook"]], width=60
        )
    )
    _ok(
        h,
        (
            "Playbook: revisar-tui" in playbook_detail,
            "el detalle identifica el playbook",
        ),
        (
            "references/checklist.md" in playbook_detail,
            "el detalle lista los ficheros",
        ),
    )

    # ── Acciones del usuario ──────────────────────────────────────
    approved = tui_memory.approve(conn, codes["proposal"])
    _ok(
        h,
        (approved["status"] == "active", "aprobar pasa la propuesta a activa"),
    )

    extra = add_memory(
        conn,
        title="Propuesta para rechazar",
        content="Se rechaza con motivo.",
        kind="decision",
        key="tui-reject",
        source="agent",
        evidence=[{"kind": "ticket", "ref": "T-0055"}],
    )
    rejected = tui_memory.reject(conn, extra["code"], "Fallo transitorio")
    still_there = {
        item["code"]
        for item in list_memories(conn, include_inactive=True, limit=100)
    }
    _ok(
        h,
        (rejected["status"] == "archived", "rechazar archiva la propuesta"),
        (extra["code"] in still_there, "rechazar no borra la memoria"),
    )

    another = add_memory(
        conn,
        title="Otra propuesta",
        content="No debe rechazarse sin motivo.",
        kind="decision",
        key="tui-no-reason",
        source="agent",
        evidence=[{"kind": "ticket", "ref": "T-0055"}],
    )
    _ok(
        h,
        (
            _falla(
                lambda: tui_memory.reject(conn, another["code"], "   "),
                ValidationError,
            ),
            "rechazar exige un motivo",
        ),
        (
            get_memory(conn, another["code"])["status"] == "proposed",
            "sin motivo la propuesta sigue intacta",
        ),
    )

    _ok(
        h,
        (
            _falla(
                lambda: tui_memory.approve(
                    conn, another["code"], actor="worker"
                ),
                PermissionError,
            ),
            "un agente no puede aprobar desde la TUI",
        ),
        (
            _falla(
                lambda: tui_memory.reject(
                    conn, another["code"], "x", actor="worker"
                ),
                PermissionError,
            ),
            "un agente no puede rechazar desde la TUI",
        ),
        (
            _falla(
                lambda: tui_memory.toggle_pin(
                    conn, codes["active"], actor="worker"
                ),
                PermissionError,
            ),
            "un agente no puede fijar desde la TUI",
        ),
        (
            _falla(
                lambda: tui_memory.archive(
                    conn, codes["active"], actor="worker"
                ),
                PermissionError,
            ),
            "un agente no puede archivar desde la TUI",
        ),
    )

    pinned = tui_memory.toggle_pin(conn, codes["active"])
    _ok(h, (bool(pinned.get("pinned")), "pin fija la memoria"))
    unpinned = tui_memory.toggle_pin(conn, codes["active"])
    _ok(h, (not unpinned.get("pinned"), "pin alterna y desfija"))

    archived = tui_memory.archive(conn, codes["active"], reason="Ya no aplica")
    archived_codes = {
        item["code"]
        for item in list_memories(conn, include_inactive=True, limit=100)
    }
    _ok(
        h,
        (archived["status"] == "archived", "archivar cambia el estado"),
        (codes["active"] in archived_codes, "archivar no borra la memoria"),
    )

    export_root = str(h.tmp / "memoria-export")
    exported = tui_memory.export(conn, codes["playbook"], root=export_root)
    _ok(
        h,
        (
            exported["path"].endswith("SKILL.md"),
            "exportar genera SKILL.md",
        ),
        (
            "revisar-tui" in exported["path"],
            "el playbook se exporta por slug",
        ),
    )

    # ── Memoria vacía y deshabilitada ─────────────────────────────
    empty = db.connect(h.tmp / "tui-memory-empty.db")
    try:
        empty_data = tui_memory.load_memories(empty)
        _ok(
            h,
            (
                empty_data["memories"] == [],
                "una memoria vacía no rompe la carga",
            ),
            (
                tui_memory.filter_memories(
                    empty_data["memories"], "todas"
                )
                == [],
                "una memoria vacía no tiene filas",
            ),
            (
                "activas 0" in tui_memory.header_lines(empty_data)[0],
                "la cabecera vacía muestra ceros",
            ),
        )
    finally:
        empty.close()

    disabled = tui_memory.load_memories(conn, {"enabled": False})
    _ok(
        h,
        (
            not disabled["enabled"],
            "la configuración deshabilitada se respeta",
        ),
        (disabled["memories"] == [], "deshabilitada no carga memorias"),
        (
            "deshabilitada" in tui_memory.header_lines(disabled)[0],
            "la cabecera avisa de la memoria deshabilitada",
        ),
    )
