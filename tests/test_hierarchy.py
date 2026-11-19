"""Épicas, tareas y renumeración."""

from __future__ import annotations

import contextlib
import io
from pathlib import Path
import cli
import tui
from mcp_layer import handlers
from ticketlib import audit as audit_lib, db, deeplink, domain, repo, status as status_lib, util
from tests.seeds import git_repo
from tests.support import Harness


def _fails(exc_type, call) -> bool:
    """True si ``call`` lanza ``exc_type`` (tabla de aserciones)."""
    try:
        call()
    except exc_type:
        return True
    return False


def run(h: Harness) -> None:
    conn = h.conn
    tmp = str(h.tmp)
    hook_repo = git_repo(Path(tmp) / "hierarchy_repo")

    # ── Épicas y tareas ───────────────────────────────────────────
    epic = repo.create_ticket(conn, title="Épica de prueba")
    task_a = repo.create_ticket(
        conn, title="Tarea A", parent_id=epic["code"]
    )
    task_b = repo.create_ticket(
        conn, title="Tarea B", parent_id=epic["code"]
    )
    roots = {
        ticket["code"]
        for ticket in repo.list_tickets(conn, roots_only=True, limit=None)
    }
    flat = {
        ticket["code"]
        for ticket in repo.list_tickets(conn, limit=None)
    }
    kids = {
        ticket["code"]
        for ticket in repo.list_tickets(
            conn, parent_id=epic["code"], limit=None
        )
    }
    h.eq(
        (
            repo.get_ticket(conn, epic["code"])["progress"],
            repo.get_ticket(conn, task_a["code"])["parent_code"],
            epic["code"] in roots and task_a["code"] not in roots,
            task_a["code"] in flat,
            kids,
        ),
        (
            {"done": 0, "confirmed": 0, "total": 2},
            epic["code"],
            True,
            True,
            {task_a["code"], task_b["code"]},
        ),
        "la épica calcula su progreso, la tarea conoce a su padre, "
        "roots_only deja fuera las tareas, sin filtro se ven y parent_id "
        "filtra los hijos",
    )

    # ── epic_check: cierre derivado de las hijas ────────────────────
    bare = repo.create_ticket(conn, title="Épica sin hijas")
    empty = status_lib.epic_check(conn, bare["code"])
    derived = status_lib.epic_check(conn, epic["code"])
    closed = repo.create_ticket(conn, title="Épica cerrada")
    closed_kid = repo.create_ticket(
        conn, title="Hija cerrada", parent_id=closed["code"]
    )
    conn.execute(
        "UPDATE tickets SET status = 'confirmado' WHERE code = ?",
        (closed_kid["code"],),
    )
    done = status_lib.epic_check(conn, closed["code"])
    mixed = repo.create_ticket(conn, title="Épica mixta")
    mix_a = repo.create_ticket(conn, title="Mixta A", parent_id=mixed["code"])
    mix_b = repo.create_ticket(conn, title="Mixta B", parent_id=mixed["code"])
    conn.execute(
        "UPDATE tickets SET status = 'cancelado' WHERE code = ?",
        (mix_a["code"],),
    )
    conn.execute(
        "UPDATE tickets SET status = 'confirmado' WHERE code = ?",
        (mix_b["code"],),
    )
    mixed_check = status_lib.epic_check(conn, mixed["code"])
    h.eq(
        (
            (empty["estado"], empty["total"], empty["camino_cierre"][0]),
            (
                derived["estado"],
                derived["hijas"]["nuevo"],
                {item["code"] for item in derived["bloqueadas"]},
            ),
            (
                done["estado"],
                not done["bloqueadas"],
                "criterio" in done,
                bool(done["camino_cierre"]),
            ),
            (
                mixed_check["estado"],
                [item["code"] for item in mixed_check["canceladas"]],
            ),
        ),
        (
            ("abierta", 0, "nuevo"),
            ("abierta", 2, {task_a["code"], task_b["code"]}),
            ("completa", True, True, True),
            ("completa", [mix_a["code"]]),
        ),
        "épica sin hijas no deriva el cierre; con hijas abiertas queda "
        "abierta y lista las bloqueadas; con todas confirmadas deriva "
        "completa; una hija cancelada no bloquea si el resto está confirmada",
    )

    # ── Confirmación con hijas abiertas y hojas ──────────────────────
    conn.execute(
        "UPDATE tickets SET status = 'nuevo' WHERE code = ?", (mix_b["code"],)
    )
    conn.execute(
        "UPDATE tickets SET status = 'verificado' WHERE code = ?",
        (mixed["code"],),
    )
    report = repo.confirm_tickets(conn, [mixed["code"]])
    leaves = {
        ticket["code"]
        for ticket in repo.list_tickets(conn, leaves_only=True, limit=None)
    }
    h.eq(
        (
            "tareas abiertas" in report["errors"].get(mixed["code"], ""),
            report["confirmed"],
            epic["code"] not in leaves and task_a["code"] in leaves,
        ),
        (True, [], True),
        "confirmar la épica con una hija abierta falla con tareas abiertas "
        "y leaves_only excluye las épicas",
    )

    # ── La jerarquía es de un solo nivel y las épicas no se trabajan ──
    h.eq(
        (
            _fails(
                domain.ValidationError,
                lambda: repo.create_ticket(
                    conn, title="Nieta", parent_id=task_a["code"]
                ),
            ),
            _fails(
                domain.ValidationError,
                lambda: repo.attach_tickets(
                    conn, task_a["code"], [epic["code"]], actor="test"
                ),
            ),
            _fails(
                domain.TransitionError,
                lambda: repo.claim_ticket(conn, epic["code"], agent="test"),
            ),
            _fails(
                domain.TransitionError,
                lambda: repo.complete_ticket(
                    conn, epic["code"], resolution="cerrada"
                ),
            ),
        ),
        (True, True, True, True),
        "solo se admite un nivel de jerarquía; una épica no se convierte "
        "en tarea, no se reclama ni se completa con tareas abiertas",
    )

    # ── attach/detach/update mueven tickets entre padre y raíz ──────
    free = repo.create_ticket(conn, title="Suelto")
    repo.attach_tickets(conn, epic["code"], [free["code"]], actor="test")
    attach_free = repo.get_ticket(conn, free["code"])
    attach_epic = repo.get_ticket(conn, epic["code"])
    repo.detach_tickets(conn, [free["code"]], actor="test")
    detach_free = repo.get_ticket(conn, free["code"])
    detach_epic = repo.get_ticket(conn, epic["code"])
    repo.update_ticket(
        conn, free["code"], parent_id=epic["code"], actor="test"
    )
    update_free = repo.get_ticket(conn, free["code"])
    repo.update_ticket(
        conn, free["code"], parent_id=None, actor="test"
    )
    unlink_free = repo.get_ticket(conn, free["code"])
    h.eq(
        (
            (attach_free["parent_code"], attach_epic["progress"]["total"]),
            (detach_free["parent_code"], detach_epic["progress"]["total"]),
            update_free["parent_code"],
            unlink_free["parent_code"],
        ),
        (
            (epic["code"], 3),
            (None, 2),
            epic["code"],
            None,
        ),
        "attach adopta tickets existentes, detach devuelve el ticket a la "
        "raíz, update acepta parent_id y con None desvincula",
    )

    # ── Progreso, cierre y confirmación de la épica ──────────────────
    for child in (task_a, task_b):
        repo.claim_ticket(conn, child["code"], agent="test")
        repo.complete_ticket(conn, child["code"], resolution="hecho")
    progress_closed = repo.get_ticket(conn, epic["code"])["progress"]
    epic_done = repo.complete_ticket(
        conn, epic["code"], resolution="objetivo cumplido"
    )
    confirmed = repo.confirm_tickets(conn, [epic["code"]])
    epic_after = repo.get_ticket(conn, epic["code"])
    again = repo.confirm_tickets(conn, [epic["code"]])
    h.eq(
        (
            progress_closed,
            epic_done["status"],
            confirmed["confirmed"],
            (epic_after["progress"]["confirmed"], epic_after["status"]),
            (again["confirmed"], again["already"]),
        ),
        (
            {"done": 2, "confirmed": 0, "total": 2},
            "completado",
            [task_a["code"], task_b["code"], epic["code"]],
            (2, "confirmado"),
            ([], [epic["code"]]),
        ),
        "el progreso cuenta las tareas cerradas, la épica se completa, "
        "confirmar confirma primero sus tareas, quedan confirmadas y "
        "confirmar dos veces es idempotente",
    )

    blocked_epic = repo.create_ticket(
        conn, title="Épica con tarea reabierta"
    )
    blocked_task = repo.create_ticket(
        conn,
        title="Tarea reabierta",
        parent_id=blocked_epic["code"],
    )
    repo.claim_ticket(conn, blocked_task["code"], agent="test")
    repo.complete_ticket(conn, blocked_task["code"], resolution="ok")
    repo.complete_ticket(conn, blocked_epic["code"], resolution="cerrada")
    repo.transition(
        conn,
        blocked_task["code"],
        "en_progreso",
        note="vuelve el fallo",
        actor="test",
    )
    blocked_report = repo.confirm_tickets(conn, [blocked_epic["code"]])
    h.ok(
        blocked_report["confirmed"] == []
        and "tareas abiertas"
        in blocked_report["errors"][blocked_epic["code"]],
        "una épica con una tarea reabierta no se confirma",
    )

    epic_open = repo.create_ticket(
        conn, title="Épica abierta", priority="urgente"
    )
    epic_task = repo.create_ticket(
        conn,
        title="Tarea urgente",
        parent_id=epic_open["code"],
        priority="urgente",
    )
    repo.transition(
        conn, epic_open["code"], "pendiente", actor="test"
    )
    repo.transition(
        conn, epic_task["code"], "pendiente", actor="test"
    )
    nxt = repo.next_ticket(conn)
    h.ok(
        nxt is not None and nxt["code"] != epic_open["code"],
        "next no devuelve épicas",
    )

    # ── Estado de la TUI con árbol ──────────────────────────────────
    tree = tui.TuiState()
    tree.load(repo.list_tickets(conn, limit=None))
    codes = [ticket["code"] for ticket in tree.visible()]
    hidden = tree.hidden_count()
    rows = {ticket["code"]: depth for ticket, depth, _ in tree.rows()}
    tree.expanded.add(epic_open["code"])
    rows_open = {ticket["code"]: depth for ticket, depth, _ in tree.rows()}
    tree.collapse_all()
    collapsed = [ticket["code"] for ticket in tree.visible()]
    tree.expand_all()
    expanded = [ticket["code"] for ticket in tree.visible()]
    tree.set_scope("abiertos")
    scoped = [ticket["code"] for ticket in tree.visible()]
    tree.set_scope("todos")
    h.eq(
        (
            epic_open["code"] in codes and epic_task["code"] not in codes,
            hidden >= 1,
            rows[epic_open["code"]],
            rows_open[epic_task["code"]],
            epic_task["code"] not in collapsed,
            epic_task["code"] in expanded,
            epic_task["code"] in scoped,
        ),
        (True, True, 0, 1, True, True, True),
        "la TUI pliega las tareas por defecto, informa de las plegadas, "
        "indenta al desplegar, collapse/expand las gestiona y con filtros "
        "la lista es plana y no esconde tareas",
    )
    h.ok(
        tree.can_confirm(
            {
                "status": "completado",
                "progress": {"done": 2, "confirmed": 0, "total": 2},
            }
        )
        and not tree.can_confirm(
            {
                "status": "completado",
                "progress": {"done": 1, "confirmed": 1, "total": 2},
            }
        )
        and not tree.can_confirm(
            {"status": "confirmado", "progress": {"done": 2, "total": 2}}
        ),
        "el padre se marca si sus tareas están cerradas",
    )

    toggle_epic = repo.create_ticket(conn, title="Épica marcable")
    toggle_a = repo.create_ticket(
        conn, title="Tarea marcable A", parent_id=toggle_epic["code"]
    )
    toggle_b = repo.create_ticket(
        conn, title="Tarea marcable B", parent_id=toggle_epic["code"]
    )
    for child in (toggle_a, toggle_b):
        repo.claim_ticket(conn, child["code"], agent="test")
        repo.complete_ticket(conn, child["code"], resolution="ok")
    repo.complete_ticket(
        conn, toggle_epic["code"], resolution="lista"
    )
    marking = tui.TuiState()
    marking.load(repo.list_tickets(conn, limit=None))
    marked = marking.focus_on(toggle_epic["code"]) and marking.toggle() is True
    selected = set(marking.selected_codes())
    marking.toggle()
    unmarked = marking.selected == set()
    marking.select_all()
    h.eq(
        (
            marked,
            selected,
            unmarked,
            {toggle_epic["code"], toggle_a["code"], toggle_b["code"]}
            <= set(marking.selected_codes()),
        ),
        (
            True,
            {toggle_epic["code"], toggle_a["code"], toggle_b["code"]},
            True,
            True,
        ),
        "marcar una épica completada marca también sus tareas plegadas, "
        "desmarcar las limpia y select_all incluye las plegadas",
    )

    child_code = repo.create_ticket(
        conn, title="Hija con código", parent_id=epic_open["code"]
    )["code"]
    linked = repo.auto_link_commit(
        conn,
        f"feat: algo ({child_code})",
        "abc1234",
        actor="test",
    )
    h.eq(
        (
            child_code.startswith(epic_open["code"] + ".")
            and child_code.endswith(".02"),
            (tui.display_code(child_code, 1), tui.display_code(child_code, 0)),
            util.normalize_code("t-14.2"),
            deeplink.parse_target("T-0014.1"),
            (linked["linked"], linked["missing"]),
        ),
        (
            True,
            (".02", child_code),
            "T-14.2",
            "T-0014.1",
            ([child_code], []),
        ),
        "las tareas heredan el prefijo con sufijo a dos dígitos, el árbol "
        "muestra solo el sufijo, normalize_code y deeplink aceptan el "
        "sufijo y el autollenado reconoce T-XXXX.N",
    )

    # ── Contrato CLI: attach, list --tree y show ────────────────────
    attach_args = cli.build_parser().parse_args(
        ["attach", "T-0001", "T-0002", "T-0003"]
    )
    out_list = io.StringIO()
    with contextlib.redirect_stdout(out_list):
        cli.main(["list", "--all"])
    out_tree = io.StringIO()
    with contextlib.redirect_stdout(out_tree):
        cli.main(["list", "--all", "--tree"])
    out_show = io.StringIO()
    with contextlib.redirect_stdout(out_show):
        cli.main(["show", epic["code"]])
    suffix = "." + task_a["code"].rsplit(".", 1)[1]
    h.eq(
        (
            (attach_args.parent, attach_args.codes),
            "oculta" in out_list.getvalue(),
            (
                suffix in out_tree.getvalue(),
                task_a["code"] not in out_tree.getvalue(),
                epic["code"] in out_tree.getvalue(),
            ),
            "Progreso: 2/2" in out_show.getvalue(),
        ),
        (
            ("T-0001", ["T-0002", "T-0003"]),
            True,
            (True, True, True),
            True,
        ),
        "el parser acepta attach con varios códigos; list avisa de las "
        "ocultas, --tree muestra solo el sufijo y show el progreso",
    )

    # ── Contrato MCP: raíces, hijos y vínculo ───────────────────────
    mcp_roots = handlers.call_tool(
        "list", {"only_roots": True, "limit": 200}
    )
    mcp_kids = handlers.call_tool(
        "list", {"parent_id": epic_open["code"], "limit": 50}
    )
    mcp_attach = handlers.call_tool(
        "update", {"code": free["code"], "parent_id": epic_open["code"]}
    )
    mcp_detach = handlers.call_tool(
        "update", {"code": free["code"], "parent_id": None}
    )
    h.eq(
        (
            mcp_roots["hidden_children"] >= 1,
            all(
                ticket["parent_id"] is None
                for ticket in mcp_roots["tickets"]
            ),
            {ticket["code"] for ticket in mcp_kids["tickets"]},
            (mcp_attach["parent_code"], mcp_detach.get("parent_code")),
        ),
        (
            True,
            True,
            {epic_task["code"], child_code},
            (epic_open["code"], None),
        ),
        "MCP list only_roots devuelve solo raíces, parent_id devuelve las "
        "tareas y update vincula o desvincula con null",
    )

    # ── Renumeración de tareas con código plano ─────────────────
    ren_epic = repo.create_ticket(conn, title="Épica renumerada")
    ren_a = repo.create_ticket(
        conn, title="Tarea A renumerada", parent_id=ren_epic["code"]
    )
    ren_b = repo.create_ticket(
        conn, title="Tarea B renumerada", parent_id=ren_epic["code"]
    )
    plan_clean = repo.renumber_plan(conn)

    conn.execute(
        "UPDATE tickets SET code = 'T-9001' WHERE id = ?", (ren_a["id"],)
    )
    conn.execute(
        "UPDATE tickets SET code = 'T-9002' WHERE id = ?", (ren_b["id"],)
    )
    conn.commit()
    plan = repo.renumber_plan(conn)
    simulated = repo.renumber_children(conn)
    simulated_code = repo.get_ticket(conn, "T-9001")["code"]
    audit_misnumbered = audit_lib.audit(
        conn, repo=str(hook_repo), days=1
    )["misnumbered"]
    adopted = repo.create_ticket(conn, title="Ticket adoptado después")
    repo.attach_tickets(
        conn, ren_epic["code"], [adopted["code"]], actor="test"
    )
    adopted_untouched = not any(
        item["code"] == adopted["code"] for item in repo.renumber_plan(conn)
    )
    repo.renumber_children(conn, apply=True, actor="test")
    applied = (
        repo.get_ticket(conn, ren_epic["code"] + ".01")["title"],
        repo.get_ticket(conn, ren_epic["code"] + ".02")["title"],
    )
    plan_after = repo.renumber_plan(conn)
    h.eq(
        (
            plan_clean,
            {item["code"]: item["new_code"] for item in plan},
            simulated == plan,
            simulated_code,
            {item["code"] for item in audit_misnumbered},
            adopted_untouched,
            applied,
            plan_after,
        ),
        (
            [],
            {
                "T-9001": ren_epic["code"] + ".01",
                "T-9002": ren_epic["code"] + ".02",
            },
            True,
            "T-9001",
            {"T-9001", "T-9002"},
            True,
            ("Tarea A renumerada", "Tarea B renumerada"),
            [],
        ),
        "renumber detecta los hijos con código plano, sin --apply solo "
        "simula, audit avisa de los planos, respeta los adoptados con "
        "attach y --apply renombra sin dejar nada pendiente",
    )

    # ── E2E del caso real: T-0023 + T-0024..T-0027 → T-0023.01..04 ──
    with h.env(TAIKI_DB=str(h.tmp / "reorg_cli.db")):
        econn = db.connect()
        real_codes = ["T-0023", "T-0024", "T-0025", "T-0026", "T-0027"]
        created = [
            repo.create_ticket(econn, title=f"Caso real {code}")
            for code in real_codes
        ]
        for ticket, code in zip(created, real_codes):
            econn.execute(
                "UPDATE tickets SET code = ? WHERE id = ?",
                (code, ticket["id"]),
            )
        econn.commit()
        repo.link_ticket(econn, "T-0027", "depende", "T-0024", actor="test")
        econn.close()
        with contextlib.redirect_stdout(io.StringIO()):
            cli.main(
                [
                    "move", "T-0024", "T-0025", "T-0026", "T-0027",
                    "--parent", "T-0023", "--renumber", "--apply",
                ]
            )
        econn = db.connect()
        moved = [
            repo.get_ticket(econn, f"T-0023.0{number}")
            for number in (1, 2, 3, 4)
        ]
        link = econn.execute(
            "SELECT ref FROM links WHERE ticket_id = ? AND kind = 'depende'",
            (moved[3]["id"],),
        ).fetchone()["ref"]
        fused = True
        try:
            econn.execute(
                "INSERT INTO tickets_fts(tickets_fts) VALUES('integrity-check')"
            )
        except Exception:
            fused = False
        h.ok(
            [ticket["title"] for ticket in moved]
            == [f"Caso real {code}" for code in real_codes[1:]]
            and all(ticket["parent_code"] == "T-0023" for ticket in moved)
            and repo.get_ticket(econn, "T-0023")["progress"]["total"] == 4
            and link == "T-0023.01"
            and fused,
            "move --renumber reproduce el caso T-0023: códigos, padre, "
            "enlace y FTS",
        )
        loose = repo.create_ticket(econn, title="Adjunto sin renumerar")
        econn.execute(
            "UPDATE tickets SET code = 'T-0040' WHERE id = ?",
            (loose["id"],),
        )
        econn.commit()
        econn.close()
        with contextlib.redirect_stdout(io.StringIO()):
            cli.main(["attach", "T-0023", "T-0040"])
        econn = db.connect()
        attached = repo.get_ticket(econn, "T-0040")
        h.ok(
            attached["parent_code"] == "T-0023"
            and attached["code"] == "T-0040",
            "attach sin flags conserva el código",
        )
