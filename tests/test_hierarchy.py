"""Épicas, tareas y renumeración."""

from __future__ import annotations

import contextlib
import io
from pathlib import Path
import cli
import tui
from mcp_layer import handlers
from ticketlib import audit as audit_lib, deeplink, domain, repo, util
from tests.seeds import git_repo
from tests.support import Harness


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
    h.ok(
        repo.get_ticket(conn, epic["code"])["progress"]
        == {"done": 0, "confirmed": 0, "total": 2},
        "la épica calcula su progreso",
    )
    h.ok(
        repo.get_ticket(conn, task_a["code"])["parent_code"]
        == epic["code"],
        "la tarea conoce a su padre",
    )
    roots = {
        ticket["code"]
        for ticket in repo.list_tickets(
            conn, roots_only=True, limit=None
        )
    }
    h.ok(
        epic["code"] in roots and task_a["code"] not in roots,
        "roots_only deja fuera las tareas",
    )
    flat = {
        ticket["code"]
        for ticket in repo.list_tickets(conn, limit=None)
    }
    h.ok(task_a["code"] in flat, "sin filtro se ven las tareas")
    kids = {
        ticket["code"]
        for ticket in repo.list_tickets(
            conn, parent_id=epic["code"], limit=None
        )
    }
    h.ok(kids == {task_a["code"], task_b["code"]}, "parent_id filtra los hijos")
    leaves = {
        ticket["code"]
        for ticket in repo.list_tickets(
            conn, leaves_only=True, limit=None
        )
    }
    h.ok(
        epic["code"] not in leaves and task_a["code"] in leaves,
        "leaves_only excluye las épicas",
    )
    h.raises(
        domain.ValidationError,
        lambda: repo.create_ticket(
            conn, title="Nieta", parent_id=task_a["code"]
        ),
        "solo se admite un nivel de jerarquía",
    )
    h.raises(
        domain.ValidationError,
        lambda: repo.attach_tickets(
            conn, task_a["code"], [epic["code"]], actor="test"
        ),
        "una épica no puede convertirse en tarea",
    )
    h.raises(
        domain.TransitionError,
        lambda: repo.claim_ticket(conn, epic["code"], agent="test"),
        "una épica no se reclama; se reclaman sus tareas",
    )
    h.raises(
        domain.TransitionError,
        lambda: repo.complete_ticket(
            conn, epic["code"], resolution="cerrada"
        ),
        "una épica no se completa con tareas abiertas",
    )

    free = repo.create_ticket(conn, title="Suelto")
    repo.attach_tickets(conn, epic["code"], [free["code"]], actor="test")
    h.ok(
        repo.get_ticket(conn, free["code"])["parent_code"]
        == epic["code"]
        and repo.get_ticket(conn, epic["code"])["progress"]["total"] == 3,
        "attach adopta tickets existentes",
    )
    repo.detach_tickets(conn, [free["code"]], actor="test")
    h.ok(
        repo.get_ticket(conn, free["code"])["parent_code"] is None
        and repo.get_ticket(conn, epic["code"])["progress"]["total"] == 2,
        "detach devuelve el ticket a la raíz",
    )
    repo.update_ticket(
        conn, free["code"], parent_id=epic["code"], actor="test"
    )
    h.ok(
        repo.get_ticket(conn, free["code"])["parent_code"] == epic["code"],
        "update acepta parent_id",
    )
    repo.update_ticket(
        conn, free["code"], parent_id=None, actor="test"
    )
    h.ok(
        repo.get_ticket(conn, free["code"])["parent_code"] is None,
        "update con parent_id=None desvincula",
    )

    for child in (task_a, task_b):
        repo.claim_ticket(conn, child["code"], agent="test")
        repo.complete_ticket(conn, child["code"], resolution="hecho")
    h.ok(
        repo.get_ticket(conn, epic["code"])["progress"]
        == {"done": 2, "confirmed": 0, "total": 2},
        "el progreso cuenta las tareas cerradas",
    )
    epic_done = repo.complete_ticket(
        conn, epic["code"], resolution="objetivo cumplido"
    )
    h.ok(epic_done["status"] == "completado", "la épica se completa")
    report = repo.confirm_tickets(conn, [epic["code"]])
    h.ok(
        report["confirmed"]
        == [task_a["code"], task_b["code"], epic["code"]],
        "confirmar la épica confirma primero sus tareas completadas",
    )
    h.ok(
        repo.get_ticket(conn, epic["code"])["progress"]["confirmed"] == 2
        and repo.get_ticket(conn, epic["code"])["status"] == "confirmado",
        "las tareas y la épica quedan confirmadas",
    )
    again = repo.confirm_tickets(conn, [epic["code"]])
    h.ok(
        again["confirmed"] == [] and again["already"] == [epic["code"]],
        "confirmar dos veces la épica es idempotente",
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

    # Estado de la TUI con árbol
    tree = tui.TuiState()
    tree.load(repo.list_tickets(conn, limit=None))
    codes = [ticket["code"] for ticket in tree.visible()]
    h.ok(
        epic_open["code"] in codes
        and epic_task["code"] not in codes,
        "la TUI pliega las tareas por defecto",
    )
    h.ok(tree.hidden_count() >= 1, "la TUI informa de las tareas plegadas")
    rows = {ticket["code"]: depth for ticket, depth, _ in tree.rows()}
    h.ok(rows[epic_open["code"]] == 0, "la épica está en el nivel 0")
    tree.expanded.add(epic_open["code"])
    rows = {ticket["code"]: depth for ticket, depth, _ in tree.rows()}
    h.ok(
        rows[epic_task["code"]] == 1,
        "al desplegar, la tarea aparece indentada",
    )
    tree.collapse_all()
    h.ok(
        epic_task["code"]
        not in [ticket["code"] for ticket in tree.visible()],
        "collapse_all pliega el árbol",
    )
    tree.expand_all()
    h.ok(
        epic_task["code"]
        in [ticket["code"] for ticket in tree.visible()],
        "expand_all despliega todas las épicas",
    )
    tree.set_scope("abiertos")
    h.ok(
        epic_task["code"]
        in [ticket["code"] for ticket in tree.visible()],
        "con filtros la lista es plana y no esconde tareas",
    )
    tree.set_scope("todos")
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
    h.ok(
        marking.focus_on(toggle_epic["code"])
        and marking.toggle() is True,
        "una épica completada se puede marcar",
    )
    h.ok(
        set(marking.selected_codes())
        == {toggle_epic["code"], toggle_a["code"], toggle_b["code"]},
        "marcar el padre marca sus tareas completadas, aunque estén plegadas",
    )
    marking.toggle()
    h.ok(marking.selected == set(), "desmarcar el padre desmarca sus tareas")
    marking.select_all()
    h.ok(
        {toggle_epic["code"], toggle_a["code"], toggle_b["code"]}
        <= set(marking.selected_codes()),
        "select_all incluye tareas plegadas",
    )

    child_code = repo.create_ticket(
        conn, title="Hija con código", parent_id=epic_open["code"]
    )["code"]
    h.ok(
        child_code.startswith(epic_open["code"] + ".")
        and child_code.endswith(".02"),
        "las tareas heredan el prefijo y un sufijo a dos dígitos",
    )
    h.ok(
        tui.display_code(child_code, 1) == ".02"
        and tui.display_code(child_code, 0) == child_code,
        "el árbol muestra solo el sufijo de la tarea",
    )
    h.ok(
        util.normalize_code("t-14.2") == "T-14.2",
        "normalize_code acepta el sufijo de tarea",
    )
    h.ok(
        deeplink.parse_target("T-0014.1") == "T-0014.1",
        "deeplink acepta códigos con sufijo",
    )
    linked = repo.auto_link_commit(
        conn,
        f"feat: algo ({child_code})",
        "abc1234",
        actor="test",
    )
    h.ok(
        linked["linked"] == [child_code] and linked["missing"] == [],
        "el autollenazado reconoce T-XXXX.N",
    )

    attach_args = cli.build_parser().parse_args(
        ["attach", "T-0001", "T-0002", "T-0003"]
    )
    h.ok(
        attach_args.parent == "T-0001"
        and attach_args.codes == ["T-0001", "T-0002", "T-0003"][1:],
        "el parser acepta attach con varios códigos",
    )
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        cli.main(["list", "--all"])
    h.ok(
        "oculta" in out.getvalue(),
        "taiki list avisa de las tareas ocultas",
    )
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        cli.main(["list", "--all", "--tree"])
    suffix = "." + task_a["code"].rsplit(".", 1)[1]
    h.ok(
        suffix in out.getvalue()
        and task_a["code"] not in out.getvalue()
        and epic["code"] in out.getvalue(),
        "taiki list --tree muestra solo el sufijo de la tarea",
    )
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        cli.main(["show", epic["code"]])
    h.ok(
        "Progreso: 2/2" in out.getvalue(),
        "taiki show muestra el progreso de la épica",
    )

    mcp_roots = handlers.call_tool(
        "list", {"only_roots": True, "limit": 200}
    )
    h.ok(
        mcp_roots["hidden_children"] >= 1
        and all(
            ticket["parent_id"] is None
            for ticket in mcp_roots["tickets"]
        ),
        "MCP list only_roots devuelve solo raíces",
    )
    mcp_kids = handlers.call_tool(
        "list", {"parent_id": epic_open["code"], "limit": 50}
    )
    h.ok(
        {ticket["code"] for ticket in mcp_kids["tickets"]}
        == {epic_task["code"], child_code},
        "MCP list parent_id devuelve las tareas",
    )
    mcp_updated = handlers.call_tool(
        "update", {"code": free["code"], "parent_id": epic_open["code"]}
    )
    h.ok(
        mcp_updated["parent_code"] == epic_open["code"],
        "MCP update vincula una tarea",
    )
    mcp_updated = handlers.call_tool(
        "update", {"code": free["code"], "parent_id": None}
    )
    h.ok(
        mcp_updated["parent_code"] is None,
        "MCP update con null desvincula",
    )

    # ── Renumeración de tareas con código plano ─────────────────
    ren_epic = repo.create_ticket(conn, title="Épica renumerada")
    ren_a = repo.create_ticket(
        conn, title="Tarea A renumerada", parent_id=ren_epic["code"]
    )
    ren_b = repo.create_ticket(
        conn, title="Tarea B renumerada", parent_id=ren_epic["code"]
    )
    h.ok(
        repo.renumber_plan(conn) == [],
        "renumber no toca las tareas ya numeradas",
    )

    conn.execute(
        "UPDATE tickets SET code = 'T-9001' WHERE id = ?", (ren_a["id"],)
    )
    conn.execute(
        "UPDATE tickets SET code = 'T-9002' WHERE id = ?", (ren_b["id"],)
    )
    conn.commit()
    plan = repo.renumber_plan(conn)
    h.ok(
        {item["code"]: item["new_code"] for item in plan}
        == {
            "T-9001": ren_epic["code"] + ".01",
            "T-9002": ren_epic["code"] + ".02",
        },
        "renumber detecta los hijos creados con código plano",
    )
    h.ok(
        repo.renumber_children(conn) == plan
        and repo.get_ticket(conn, "T-9001")["code"] == "T-9001",
        "renumber sin --apply solo simula",
    )
    audit_misnumbered = audit_lib.audit(
        conn, repo=str(hook_repo), days=1
    )["misnumbered"]
    h.ok(
        {item["code"] for item in audit_misnumbered}
        == {"T-9001", "T-9002"},
        "audit avisa de las tareas con código plano",
    )

    adopted = repo.create_ticket(conn, title="Ticket adoptado después")
    repo.attach_tickets(
        conn, ren_epic["code"], [adopted["code"]], actor="test"
    )
    h.ok(
        not any(
            item["code"] == adopted["code"]
            for item in repo.renumber_plan(conn)
        ),
        "renumber respeta los tickets adoptados con attach",
    )

    repo.renumber_children(conn, apply=True, actor="test")
    h.ok(
        repo.get_ticket(conn, ren_epic["code"] + ".01")["title"]
        == "Tarea A renumerada"
        and repo.get_ticket(conn, ren_epic["code"] + ".02")["title"]
        == "Tarea B renumerada",
        "renumber --apply renombra los códigos",
    )
    h.ok(
        repo.renumber_plan(conn) == [],
        "tras renumerar no queda nada pendiente",
    )
