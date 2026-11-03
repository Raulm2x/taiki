"""Concurrencia, transacciones y jerarquía de subtareas."""

from __future__ import annotations

import subprocess
import threading
import time

from ticketlib import db, domain, render, repo, workspace
from ticketlib.store import queries, relations, workflow
from tests.seeds import git_repo
from tests.support import Harness


def run(h: Harness) -> None:
    conn = h.conn
    tmp = str(h.tmp)

    # ── 3. Concurrency & Reentrant Transactions Suite ────────────────────
    # Reentrancia directa de db.tx sin OperationalError
    with db.tx(conn):
        conn.execute("INSERT INTO meta(key, value) VALUES ('reentrant_l1', '1')")
        with db.tx(conn):
            conn.execute("INSERT INTO meta(key, value) VALUES ('reentrant_l2', '2')")
            with db.tx(conn):
                conn.execute("INSERT INTO meta(key, value) VALUES ('reentrant_l3', '3')")
    h.ok(
        db.meta_get(conn, "reentrant_l1") == "1"
        and db.meta_get(conn, "reentrant_l2") == "2"
        and db.meta_get(conn, "reentrant_l3") == "3",
        "los tres niveles de tx reentrante persistieron",
    )

    # Reversión completa (ROLLBACK) al lanzar excepción en tx anidada
    try:
        with db.tx(conn):
            conn.execute("INSERT INTO meta(key, value) VALUES ('rollback_outer', 'outer')")
            with db.tx(conn):
                conn.execute("INSERT INTO meta(key, value) VALUES ('rollback_inner', 'inner')")
                raise RuntimeError("Fallo provocado en tx anidada")
    except RuntimeError:
        pass
    h.ok(
        db.meta_get(conn, "rollback_outer") is None
        and db.meta_get(conn, "rollback_inner") is None,
        "una excepción en tx anidada revierte outer e inner",
    )

    # ── El trigger FTS solo reindexa title/body/code ─────────────────────
    fts_ticket = repo.create_ticket(conn, title="índice viejo", body="cuerpo base")
    traced: list[str] = []
    conn.set_trace_callback(traced.append)
    try:
        conn.execute(
            "UPDATE tickets SET heartbeat_at = ?, updated_at = ? WHERE code = ?",
            (
                "2026-01-01T00:00:00-05:00",
                "2026-01-01T00:00:00-05:00",
                fts_ticket["code"],
            ),
        )
        heartbeat_trace = list(traced)
        traced.clear()
        conn.execute(
            "UPDATE tickets SET title = ? WHERE code = ?",
            ("índice nuevo", fts_ticket["code"]),
        )
        title_trace = list(traced)
    finally:
        conn.set_trace_callback(None)
    h.ok(
        not any("tickets_fts" in stmt for stmt in heartbeat_trace)
        and any("tickets_fts" in stmt for stmt in title_trace),
        "solo el update de title reindexa FTS",
    )
    h.ok(
        fts_ticket["code"] in repo.search_codes(conn, "nuevo")
        and fts_ticket["code"] not in repo.search_codes(conn, "viejo"),
        "la búsqueda ve el título nuevo y deja de ver el viejo",
    )

    # ── Índices para las consultas calientes ─────────────────────────────
    indexes = {
        row["name"]
        for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'index'"
        )
    }
    h.ok(
        {"idx_events_ts", "idx_events_action", "idx_tickets_updated"} <= indexes,
        "existen los índices de events(ts/action) y tickets(updated_at)",
    )
    plans = {
        "action": conn.execute(
            "EXPLAIN QUERY PLAN SELECT note FROM events "
            "WHERE action = 'renumerar'"
        ).fetchall(),
        "updated": conn.execute(
            "EXPLAIN QUERY PLAN SELECT MAX(updated_at) FROM tickets"
        ).fetchall(),
        "day": conn.execute(
            "EXPLAIN QUERY PLAN SELECT substr(e.ts, 1, 10) AS day, COUNT(*) "
            "FROM events e WHERE e.ts >= ? AND e.ts < ? "
            "AND e.ticket_id NOT IN "
            "(SELECT id FROM tickets WHERE archived = 1) GROUP BY day",
            ("2026-01-01", "2027-01-01"),
        ).fetchall(),
    }
    details = {
        key: " | ".join(row["detail"] for row in rows)
        for key, rows in plans.items()
    }
    h.ok(
        "idx_events_action" in details["action"]
        and "idx_tickets_updated" in details["updated"]
        and "idx_events_ts" in details["day"]
        and "SCAN events" not in details["day"],
        "los planes usan idx_events_action, idx_tickets_updated e idx_events_ts",
    )
    h.ok(
        not any(
            row["name"] == "idx_tags_tag"
            for row in conn.execute("PRAGMA index_list(tags)")
        ),
        "no se añade un índice redundante para tags(tag)",
    )

    # ── complete resuelve git fuera de la transacción ────────────────────
    slow = repo.create_ticket(conn, title="Cierre con git lento")
    repo.claim_ticket(conn, slow["code"], agent="opencode")
    resolving = threading.Event()
    release = threading.Event()
    original_workflow = workflow.resolve_commit
    original_relations = relations.resolve_commit

    def slow_resolve(ref, cwd=None):
        resolving.set()
        # El hilo principal libera tras intentar el lock: así el test no
        # depende de un sleep fijo para mantener viva la resolución de git.
        release.wait(timeout=5)
        return "f" * 40

    workflow.resolve_commit = slow_resolve
    relations.resolve_commit = slow_resolve
    outcome: dict[str, object] = {}

    def closer() -> None:
        worker = db.connect()
        try:
            outcome["ticket"] = repo.complete_ticket(
                worker, slow["code"], resolution="listo", commit="abc1234"
            )
        finally:
            worker.close()

    thread = threading.Thread(target=closer)
    try:
        thread.start()
        h.ok(resolving.wait(timeout=2), "el cierre llega a resolver el commit")
        other = db.connect()
        started = time.perf_counter()
        other.execute("BEGIN IMMEDIATE")
        waited = time.perf_counter() - started
        other.execute("ROLLBACK")
        other.close()
        release.set()
        thread.join(timeout=5)
    finally:
        workflow.resolve_commit = original_workflow
        relations.resolve_commit = original_relations
    h.ok(
        waited < 0.1,
        f"complete no retiene el lock durante git ({waited * 1000:.0f} ms)",
    )
    closed_slow = outcome.get("ticket") or {}
    h.ok(
        closed_slow.get("status") == "completado"
        and any(
            link["kind"] == "commit" for link in closed_slow.get("links", [])
        ),
        "complete enlaza el commit resuelto",
    )

    # ── TicketFilter comparte WHERE y la proyección cachea columnas ─────
    filt = repo.TicketFilter(statuses=["nuevo"])
    h.eq(
        len(repo.list_tickets(conn, filters=filt, limit=None)),
        repo.count_tickets(conn, filters=filt),
        "list y count comparten el mismo TicketFilter",
    )
    queries._TICKET_COLUMNS = None
    repo.list_tickets(conn, limit=1)
    cached = queries._TICKET_COLUMNS
    repo.list_tickets(conn, limit=1)
    h.ok(
        cached is not None and queries._TICKET_COLUMNS is cached,
        "la proyección lean cachea las columnas",
    )

    # ── Filtros en tabla: una fila por combinación ──────────────────────
    matrix = db.connect(":memory:")
    try:
        alpha = repo.create_ticket(
            matrix, title="Alfa", kind="bug", priority="alta",
            project="matriz", tags=["uno"],
        )
        beta = repo.create_ticket(
            matrix, title="Beta", kind="funcionalidad", priority="baja",
            project="matriz", tags=["dos"],
        )
        gamma = repo.create_ticket(
            matrix, title="Gamma", kind="bug", priority="media", project="otro",
        )
        filter_cases = (
            (repo.TicketFilter(project="matriz"), {alpha["code"], beta["code"]}),
            (repo.TicketFilter(kind="bug"), {alpha["code"], gamma["code"]}),
            (repo.TicketFilter(priority="alta"), {alpha["code"]}),
            (repo.TicketFilter(tag="dos"), {beta["code"]}),
            (repo.TicketFilter(tags=["uno", "dos"]), {alpha["code"], beta["code"]}),
            (repo.TicketFilter(query="Gamma"), {gamma["code"]}),
            (
                repo.TicketFilter(statuses=["nuevo"], project="matriz"),
                {alpha["code"], beta["code"]},
            ),
        )
        for filt, expected in filter_cases:
            h.eq(
                {
                    ticket["code"]
                    for ticket in repo.list_tickets(
                        matrix, filters=filt, limit=None
                    )
                },
                expected,
                f"filtro de tabla {filt}",
            )
    finally:
        matrix.close()

    # ── Renumerado estructurado con fallback a la nota antigua ──────────
    structured = db.connect(":memory:")
    try:
        for title in ("Uno", "Dos", "Tres"):
            repo.create_ticket(structured, title=title)
        renamed = repo.get_ticket(structured, "T-0003")
        structured.execute(
            "UPDATE tickets SET code = 'T-0002.01' WHERE id = ?",
            (renamed["id"],),
        )
        structured.execute(
            "INSERT INTO links(ticket_id, kind, ref, created_at) "
            "VALUES (?, 'otro', ?, ?)",
            (renamed["id"], "renumerado:T-0003->T-0002.01", "2026-01-01"),
        )
        db.meta_set(structured, "next_ticket_number", "4")
        reasons = {
            item["code"]: item["reason"]
            for item in queries.missing_codes(structured)
        }
        h.eq(
            reasons.get("T-0003"),
            "renumerado a T-0002.01",
            "missing_codes lee el renumerado estructurado",
        )
    finally:
        structured.close()

    # Atomicidad en claim_ticket
    t_atomic = repo.create_ticket(conn, title="Ticket para prueba atómica de claim")
    h.ok(t_atomic["status"] == "nuevo", "ticket inicia en estado nuevo")
    h.ok(t_atomic["assigned_to"] is None, "ticket inicia sin asignación")

    # Primer reclamo por agente alfa
    claim_a = repo.claim_ticket(conn, t_atomic["code"], agent="agente:alfa")
    h.ok(claim_a["status"] == "en_progreso", "reclamar transiciona a en_progreso")
    h.ok(claim_a["assigned_to"] == "agente:alfa", "asignado correctamente a agente:alfa")

    # Reclamo idempotente por el mismo agente alfa
    claim_a_repeat = repo.claim_ticket(conn, t_atomic["code"], agent="agente:alfa")
    h.ok(claim_a_repeat["assigned_to"] == "agente:alfa", "reclamo por el mismo agente es idempotente")
    h.ok(claim_a_repeat["status"] == "en_progreso", "mantiene estado en_progreso")

    # Intento de doble reclamo conflictivo por agente beta sin flag force -> debe lanzar TransitionError
    h.raises(
        domain.TransitionError,
        lambda: repo.claim_ticket(conn, t_atomic["code"], agent="agente:beta"),
        "doble claim por agente distinto sin force debe fallar con TransitionError",
    )

    # Reclamo con force=True por agente beta -> debe reasignar exitosamente
    claim_forced = repo.claim_ticket(conn, t_atomic["code"], agent="agente:beta", force=True)
    h.ok(claim_forced["assigned_to"] == "agente:beta", "claim con force=True reasigna a agente:beta")
    h.ok(claim_forced["status"] == "en_progreso", "mantiene estado en_progreso tras forzar claim")

    # Intento de reclamar tickets en estados cerrados/terminales
    t_term = repo.create_ticket(conn, title="Ticket completado para probar claim")
    repo.claim_ticket(conn, t_term["code"], agent="agente:test")
    repo.complete_ticket(conn, t_term["code"], resolution="Completado para pruebas")
    h.raises(
        domain.TransitionError,
        lambda: repo.claim_ticket(conn, t_term["code"], agent="agente:gamma"),
        "reclamar ticket completado debe fallar con TransitionError",
    )

    t_cancelled = repo.create_ticket(conn, title="Ticket cancelado para probar claim")
    repo.transition(conn, t_cancelled["code"], "cancelado")
    h.raises(
        domain.TransitionError,
        lambda: repo.claim_ticket(conn, t_cancelled["code"], agent="agente:gamma"),
        "reclamar ticket cancelado debe fallar con TransitionError",
    )

    # ── 4. Subtasks Hierarchy Suite ──────────────────────────────────────
    # Creación de ticket padre (épica/tarea contenedora)
    epic = repo.create_ticket(
        conn,
        title="Épica: Arquitectura Modular del Sistema",
        body="Coordinación de refactorizaciones.",
        kind="funcionalidad",
        priority="alta",
    )
    h.ok(epic["parent_code"] is None, "ticket padre no tiene parent_code")
    h.ok(epic["subtasks"] == [], "ticket recién creado tiene lista de subtasks vacía")

    # Creación de subtareas hijas enlazadas mediante parent_id
    sub1 = repo.create_ticket(
        conn,
        title="Subtarea 1: Modularizar queries de lectura",
        kind="funcionalidad",
        priority="alta",
        parent_id=epic["code"],
    )
    sub2 = repo.create_ticket(
        conn,
        title="Subtarea 2: Modularizar renderizado de tablas",
        kind="funcionalidad",
        priority="media",
        parent_id=epic["code"],
    )
    sub3 = repo.create_ticket(
        conn,
        title="Subtarea 3: Modularizar handlers MCP",
        kind="bug",
        priority="baja",
        parent_id=epic["code"],
    )

    # Verificación en los tickets hijos
    sub1_detail = repo.get_ticket(conn, sub1["code"])
    h.ok(sub1_detail["parent_code"] == epic["code"], "sub1 reporta parent_code del padre")
    h.ok(sub1_detail["parent_id"] is not None, "sub1 tiene parent_id asignado")
    h.ok(sub1_detail["subtasks"] == [], "subtarea hija sin descendientes tiene subtasks vacía")

    # Verificación en el ticket padre tras agregar hijos
    epic_detail = repo.get_ticket(conn, epic["code"])
    h.ok("subtasks" in epic_detail, "get_ticket en padre debe incluir clave 'subtasks'")
    h.ok(isinstance(epic_detail["subtasks"], list), "subtasks debe ser una lista")
    h.ok(len(epic_detail["subtasks"]) == 3, "padre debe reportar exactamente 3 subtareas")

    # Verificación de la estructura de cada elemento en subtasks
    sub_codes = [s["code"] for s in epic_detail["subtasks"]]
    h.ok(sub_codes == [sub1["code"], sub2["code"], sub3["code"]], "orden y códigos de subtareas coinciden")
    first_sub = epic_detail["subtasks"][0]
    h.ok(first_sub["code"] == sub1["code"], "subtask code coincide")
    h.ok(first_sub["title"] == "Subtarea 1: Modularizar queries de lectura", "subtask title coincide")
    h.ok(first_sub["status"] == "nuevo", "subtask status inicial es nuevo")
    h.ok(first_sub["priority"] == "alta", "subtask priority coincide")
    h.ok(first_sub["kind"] == "funcionalidad", "subtask kind coincide")

    # El cambio de estado de una subtarea se refleja en subtasks del padre
    repo.claim_ticket(conn, sub1["code"], agent="agente:worker")
    epic_detail_updated = repo.get_ticket(conn, epic["code"])
    h.ok(epic_detail_updated["subtasks"][0]["status"] == "en_progreso", "cambio de estado de subtarea se refleja en el padre")

    # Formateo en render_detail
    rendered_parent = render.render_detail(epic_detail_updated)
    h.ok("Subtareas:" in rendered_parent, "render_detail de padre incluye cabecera 'Subtareas:'")
    h.ok(sub1["code"] in rendered_parent, "render_detail incluye código de subtarea 1")
    h.ok("[en_progreso]" in rendered_parent, "render_detail incluye estado actualizado de subtarea 1")
    h.ok(sub2["code"] in rendered_parent, "render_detail incluye código de subtarea 2")
    h.ok("[nuevo]" in rendered_parent, "render_detail incluye estado de subtarea 2")

    # render_detail de ticket sin subtareas NO incluye la sección Subtareas:
    rendered_child = render.render_detail(sub1_detail)
    h.ok("Subtareas:" not in rendered_child, "render_detail de ticket sin subtareas no incluye cabecera 'Subtareas:'")
    h.ok(f"Padre: {epic['code']}" in rendered_child, "render_detail de hijo muestra 'Padre: <código>'")

    # Intento de crear subtarea con parent_id inexistente debe fallar
    h.raises(
        domain.NotFoundError,
        lambda: repo.create_ticket(conn, title="Subtarea huérfana", parent_id="T-9999"),
        "parent_id inexistente debe fallar con NotFoundError",
    )

    # ── get con límites conserva la franja más reciente ─────────────────
    bounded = repo.create_ticket(conn, title="Límites de secciones")
    stamp = "2026-01-01T00:00:00+01:00"
    with db.tx(conn):
        for index in range(1, 26):
            conn.execute(
                "INSERT INTO links(ticket_id, kind, ref, created_at) "
                "VALUES (?, 'url', ?, ?)",
                (bounded["id"], f"L{index}", stamp),
            )
            conn.execute(
                "INSERT INTO tickets(code, title, status, priority, kind, "
                "parent_id, created_at, updated_at) "
                "VALUES (?, ?, 'pendiente', 'media', 'otro', ?, ?, ?)",
                (f"{bounded['code']}.{index:02d}", f"Sub {index}", bounded["id"], stamp, stamp),
            )

    recent = repo.get_ticket(
        conn, bounded["code"], links_limit=3, subtasks_limit=3
    )
    h.eq(
        (
            [link["ref"] for link in recent["links"]],
            [sub["code"] for sub in recent["subtasks"]],
            (recent["links_total"], recent["subtasks_total"]),
            recent["progress"]["total"],
        ),
        (
            ["L23", "L24", "L25"],
            [f"{bounded['code']}.{index:02d}" for index in (23, 24, 25)],
            (25, 25),
            25,
        ),
        "límites devuelven lo más reciente, los totales y progress completos",
    )
    default_limit = repo.get_ticket(
        conn, bounded["code"], links_limit=20, subtasks_limit=20
    )
    unbounded = repo.get_ticket(
        conn, bounded["code"], links_limit=0, subtasks_limit=0
    )
    h.eq(
        (
            [link["ref"] for link in default_limit["links"]],
            [link["ref"] for link in unbounded["links"]],
        ),
        (
            [f"L{index}" for index in range(6, 26)],
            [f"L{index}" for index in range(1, 26)],
        ),
        "el límite por defecto es 20 y sin límite se conserva el orden completo",
    )

    # ── Dependencias entre tareas y registro de capacidades ──────────────
    base = repo.create_ticket(conn, title="Base", project="deps")
    dependent = repo.create_ticket(conn, title="Dependiente", project="deps")
    repo.link_ticket(conn, dependent["code"], "depende", base["code"])
    blocked = repo.get_ticket(conn, dependent["code"])
    h.ok(
        blocked["depends_on"] == [base["code"]]
        and blocked["blocked_by"] == [base["code"]]
        and blocked["ready"] is False,
        "una dependencia abierta bloquea la tarea",
    )
    h.ok(
        base["code"]
        in {ticket["code"] for ticket in repo.list_tickets(conn, ready=True)}
        and dependent["code"]
        not in {ticket["code"] for ticket in repo.list_tickets(conn, ready=True)},
        "list ready deja fuera las tareas bloqueadas",
    )
    h.ok(
        repo.next_ticket(conn, project="deps", ready=True)["code"] == base["code"],
        "next salta la tarea con dependencias abiertas",
    )
    repo.transition(conn, base["code"], "en_progreso", actor="test")
    repo.complete_ticket(conn, base["code"], resolution="base lista")
    h.ok(
        not repo.get_ticket(conn, dependent["code"])["ready"],
        "completar no desbloquea: falta la verificación",
    )
    repo.transition(
        conn,
        base["code"],
        "verificado",
        note="gates verdes",
        actor="conductor",
    )
    h.ok(
        repo.get_ticket(conn, dependent["code"])["ready"]
        and repo.next_ticket(conn, project="deps", ready=True)["code"]
        == dependent["code"],
        "al verificar la dependencia la tarea pasa a lista",
    )

    cycle_a = repo.create_ticket(conn, title="Ciclo A", project="deps")
    cycle_b = repo.create_ticket(conn, title="Ciclo B", project="deps")
    repo.link_ticket(conn, cycle_a["code"], "depende", cycle_b["code"])
    h.raises(
        domain.ValidationError,
        lambda: repo.link_ticket(
            conn, cycle_b["code"], "depende", cycle_a["code"]
        ),
        "una dependencia circular se rechaza",
    )
    h.raises(
        domain.ValidationError,
        lambda: repo.link_ticket(
            conn, cycle_a["code"], "depende", cycle_a["code"]
        ),
        "un ticket no puede depender de sí mismo",
    )
    h.raises(
        domain.NotFoundError,
        lambda: repo.link_ticket(
            conn, cycle_a["code"], "depende", "T-9999"
        ),
        "una dependencia a un ticket inexistente falla",
    )

    from ticketlib import agents

    default_table = agents.registry()
    h.ok(
        set(agents.DEFAULT_AGENTS) <= set(default_table)
        and default_table["worker"]["max_parallel"] >= 1,
        "el registro por defecto valida y expone capacidades",
    )
    custom = agents.registry(
        {"agents": {"worker": {"max_parallel": 3, "cost": 2.5}}}
    )
    h.eq(
        (custom["worker"]["max_parallel"], custom["worker"]["cost"]),
        (3, 2.5),
        "la configuración sobrescribe el registro",
    )
    h.ok(
        all(
            agents.recommend(kind)["name"] == agents.assign(kind)["agent"]
            for kind in ("documentacion", "bug")
        )
        and agents.assign("bug")["agent"] == "worker-high"
        and agents.assign("bug")["model"]
        and agents.assign("bug")["steps"] == 40,
        "recomendar coincide con assign y expone modelo y steps reales",
    )
    h.raises(
        domain.ValidationError,
        lambda: agents.capability("desconocido"),
        "un agente desconocido falla con error claro",
    )
    h.raises(
        domain.ValidationError,
        lambda: agents.registry({"agents": {"worker": {"max_parallel": 0}}}),
        "el registro rechaza paralelismo inválido",
    )
    h.raises(
        domain.ValidationError,
        lambda: agents.registry(
            {"agents": {"worker": {"strengths": ["inventado"]}}}
        ),
        "el registro rechaza tipos desconocidos",
    )

    # ── Aislamiento, scope y reversión (esquema v7) ─────────────────────
    legacy = db.connect(h.tmp / "v6-isolation.db")
    try:
        previous = repo.create_ticket(legacy, title="Ticket anterior a v7")
        for column in ("isolation", "resources", "reversal"):
            legacy.execute(f"ALTER TABLE tickets DROP COLUMN {column}")
        legacy.execute("PRAGMA user_version = 6")
        db.migrate(legacy)
        migrated = repo.get_ticket(legacy, previous["code"])
        first = db.schema_version(legacy)
        db.migrate(legacy)
        h.eq(
            (
                first,
                (migrated["isolation"], migrated["resources"], migrated["reversal"]),
                db.schema_version(legacy),
            ),
            (7, ("worktree", [], None), 7),
            "la migración sube a v7, deja worktree sin recursos y es idempotente",
        )
    finally:
        legacy.close()

    scoped = repo.create_ticket(
        conn,
        title="Ticket con scope",
        isolation="scope",
        resources=["systemd:user/taiki.service", "package:git"],
        reversal='{"kind": "manual", "ref": "T-0001"}',
    )
    h.eq(
        (
            scoped["isolation"],
            scoped["resources"],
            scoped["reversal"],
            repo.create_ticket(conn, title="Aislamiento por defecto")[
                "isolation"
            ],
        ),
        (
            "scope",
            ["systemd:user/taiki.service", "package:git"],
            {"kind": "manual", "ref": "T-0001"},
            "worktree",
        ),
        "create guarda isolation/recursos/reversión y usa worktree por defecto",
    )
    updated_scope = repo.update_ticket(
        conn,
        scoped["code"],
        isolation="none",
        resources=["service:sshd", "systemd:system/nginx.service"],
        reversal={"kind": "command", "command": "systemctl restart nginx"},
    )
    cleared = repo.update_ticket(
        conn, scoped["code"], resources=[], reversal=None
    )
    h.eq(
        (
            updated_scope["isolation"],
            updated_scope["resources"],
            updated_scope["reversal"]["kind"],
            cleared["resources"],
            cleared["reversal"],
        ),
        ("none", ["service:sshd", "systemd:system/nginx.service"], "command", [], None),
        "update reemplaza y también puede vaciar isolation, recursos y reversión",
    )
    invalid = [
        lambda: repo.create_ticket(conn, title="Mal", isolation="docker"),
        lambda: repo.create_ticket(
            conn, title="Mal", resources=["file:/etc/passwd"]
        ),
        lambda: repo.create_ticket(
            conn, title="Mal", resources=["package:git", "package:git"]
        ),
        lambda: repo.create_ticket(conn, title="Mal", resources=["package:"]),
        lambda: repo.create_ticket(
            conn,
            title="Mal",
            resources=[
                f"package:pkg{i}" for i in range(domain.MAX_RESOURCES + 1)
            ],
        ),
        lambda: repo.create_ticket(
            conn, title="Mal", reversal={"ref": "sin kind"}
        ),
        lambda: repo.create_ticket(conn, title="Mal", reversal="{no-json}"),
    ]
    rejected = 0
    for call in invalid:
        try:
            call()
        except domain.ValidationError:
            rejected += 1
    h.eq(
        rejected,
        len(invalid),
        "aislamiento, recursos y reversión rechazan valores inválidos",
    )

    # ── reject --revert: sólo live y sólo git-revert se ejecuta ─────────
    no_reversal = repo.create_ticket(conn, title="Sin reversión", isolation="none")
    h.raises(
        domain.ValidationError,
        lambda: workflow.revert_ticket(conn, no_reversal["code"], "sin reversal"),
        "revert sin reversión registrada falla",
    )
    h.raises(
        domain.PermissionDenied,
        lambda: workflow.revert_ticket(
            conn, no_reversal["code"], "agente", actor_type="agente"
        ),
        "revert está reservado al usuario",
    )
    worktree_ticket = repo.create_ticket(conn, title="Revert worktree")
    repo.update_ticket(
        conn,
        worktree_ticket["code"],
        reversal={"kind": "copy", "command": "true"},
    )
    h.raises(
        domain.ValidationError,
        lambda: workflow.revert_ticket(conn, worktree_ticket["code"], "worktree"),
        "revert en worktree se rechaza y pide reject normal",
    )

    revert_repo = git_repo(h.tmp / "revert-repo")
    note = revert_repo / "nota.txt"
    note.write_text("uno\n", encoding="utf-8")
    subprocess.run(
        ["git", "-C", str(revert_repo), "add", "nota.txt"], check=True
    )
    subprocess.run(
        ["git", "-C", str(revert_repo), "commit", "-qm", "uno"], check=True
    )
    note.write_text("dos\n", encoding="utf-8")
    subprocess.run(
        ["git", "-C", str(revert_repo), "commit", "-qam", "dos"], check=True
    )
    revert_sha = subprocess.run(
        ["git", "-C", str(revert_repo), "rev-parse", "HEAD"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    auto = repo.create_ticket(conn, title="Revert auto", isolation="none")
    repo.update_ticket(
        conn,
        auto["code"],
        reversal={
            "kind": "git-revert",
            "ref": str(revert_repo),
            "command": f"git -C {revert_repo} revert --no-edit {revert_sha}",
        },
    )
    auto_result = workflow.revert_ticket(conn, auto["code"], "auto")
    h.ok(
        auto_result["manual"] is False
        and auto_result["ticket"]["status"] == "cancelado"
        and note.read_text(encoding="utf-8") == "uno\n",
        "git-revert se ejecuta, revierte el fichero y cancela el ticket",
    )

    note.write_text("tres\n", encoding="utf-8")
    subprocess.run(
        ["git", "-C", str(revert_repo), "commit", "-qam", "tres"], check=True
    )
    placeholder_sha = subprocess.run(
        ["git", "-C", str(revert_repo), "rev-parse", "HEAD"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    placeholder = repo.create_ticket(
        conn, title="Revert placeholder", isolation="none"
    )
    repo.update_ticket(
        conn,
        placeholder["code"],
        reversal={
            "kind": "git-revert",
            "ref": str(revert_repo),
            "command": f"git -C {revert_repo} revert --no-edit <sha>",
        },
    )
    missing_sha = False
    try:
        workflow.revert_ticket(conn, placeholder["code"], "sin enlace")
    except domain.ValidationError:
        missing_sha = True
    repo.link_ticket(
        conn,
        placeholder["code"],
        "external",
        f"{revert_repo.name}@{placeholder_sha}",
    )
    placeholder_result = workflow.revert_ticket(conn, placeholder["code"], "enlace")
    h.ok(
        missing_sha
        and not placeholder_result["manual"]
        and placeholder_result["ticket"]["status"] == "cancelado"
        and note.read_text(encoding="utf-8") == "uno\n",
        "<sha> exige un external repo@sha y con él ejecuta el revert",
    )

    manual = repo.create_ticket(conn, title="Revert manual", isolation="scope", paths=["src/"])
    manual_target = h.tmp / "no-crear.txt"
    repo.update_ticket(
        conn,
        manual["code"],
        reversal={"kind": "copy", "ref": "snap", "command": f"touch {manual_target}"},
    )
    manual_result = workflow.revert_ticket(conn, manual["code"], "manual")
    h.ok(
        manual_result["manual"] is True
        and manual_result["ticket"]["status"] == "cancelado"
        and not manual_target.exists(),
        "copy no se ejecuta: devuelve manual=True y deja el ticket cancelado",
    )


    h.eq(
        (
            domain.risk_for(["/etc/nginx/nginx.conf"], []),
            domain.risk_for(["/var/lib/docker"], ["systemd:system/nginx.service"]),
            domain.risk_for(["/boot/vmlinuz"], ["package:git"]),
            domain.risk_for(["~/.config/hypr"], ["systemd:user/taiki.service"]),
            domain.risk_for(["ticketlib/domain.py"], ["service:sshd"]),
        ),
        ("high", "high", "high", "medium", "low"),
        "risk_for deriva high/medium/low de paths y recursos",
    )
    risk_ticket = repo.create_ticket(
        conn,
        title="Autorizable",
        isolation="none",
        paths=["/etc/nginx/nginx.conf"],
    )
    risk_claimed = repo.claim_ticket(conn, risk_ticket["code"], agent="opencode")
    h.ok(
        not workflow.authorization_active(conn, risk_claimed),
        "sin evento autorizar no hay autorización",
    )
    h.raises(
        domain.PermissionDenied,
        lambda: workflow.authorize_ticket(
            conn, risk_ticket["code"], "reiniciar nginx", actor_type="agente"
        ),
        "solo el usuario puede autorizar",
    )
    authorized = workflow.authorize_ticket(
        conn, risk_ticket["code"], "reiniciar nginx"
    )
    h.ok(
        workflow.authorization_active(conn, authorized)
        and any(
            event["action"] == "autorizar" and event["note"] == "reiniciar nginx"
            for event in authorized["events"]
        ),
        "authorize deja evento y activa la autorización",
    )
    repo.transition(
        conn,
        risk_ticket["code"],
        "cancelado",
        note="fin del escenario de autorización",
        actor="usuario",
        actor_type="usuario",
    )

    # ── Revisión especial de rutas sensibles (aviso, no gate) ───────────
    h.eq(
        (
            domain.requires_review(["ticketlib/domain.py"]),
            domain.requires_review(["ticketlib/guard/policy.py"]),
            domain.requires_review([".opencode/agents/worker-high.md"]),
            domain.requires_review(["tests/coverage_gate.py"]),
            domain.requires_review(["ticketlib/*.py"]),
            domain.requires_review(["commands/tickets.py"]),
        ),
        (True, True, True, True, True, False),
        "requires_review detecta exactos, prefijos, globs y no sensibles",
    )
    sensitive = repo.create_ticket(
        conn,
        title="Sensible sin revisar",
        isolation="none",
        paths=["ticketlib/guard/policy.py"],
    )
    repo.claim_ticket(conn, sensitive["code"], agent="opencode")
    h.raises(
        domain.PermissionDenied,
        lambda: workflow.review_ticket(
            conn, sensitive["code"], "revisado", actor_type="agente"
        ),
        "solo el usuario puede revisar",
    )
    h.raises(
        domain.ValidationError,
        lambda: workflow.review_ticket(conn, sensitive["code"], "  "),
        "la revisión exige motivo",
    )
    before = workflow.review_active(conn, sensitive)
    repo.transition(
        conn, sensitive["code"], "completado", note="listo", actor="opencode"
    )
    verified = workflow.transition(
        conn, sensitive["code"], "verificado", note="tests"
    )
    h.ok(
        verified["status"] == "verificado"
        and not workflow.review_active(conn, verified)
        and domain.requires_review(verified["paths"]),
        "un ticket sensible pasa a verificado sin taiki review; "
        "requires_review queda como aviso",
    )
    reviewed = workflow.review_ticket(
        conn, sensitive["code"], "revisado por el usuario"
    )
    h.ok(
        not before
        and workflow.review_active(conn, reviewed)
        and any(
            event["action"] == "revisar"
            and event["note"] == "revisado por el usuario"
            for event in reviewed["events"]
        ),
        "review_active pasa de falso a verdadero y deja evento",
    )
    h.eq(
        reviewed["status"],
        "verificado",
        "revisar después de verificar es opcional y no cambia el estado",
    )

    # ── complete live: exige commit o external enlazado ────────────────
    live_none = repo.create_ticket(
        conn, title="Live sin evidencia", isolation="none"
    )
    repo.claim_ticket(conn, live_none["code"], agent="opencode")
    h.raises(
        domain.ValidationError,
        lambda: repo.complete_ticket(
            conn, live_none["code"], resolution="sin commit"
        ),
        "complete en aislamiento none sin commit falla",
    )
    live_external = repo.create_ticket(
        conn,
        title="Live con external",
        isolation="scope",
        paths=["src/"],
    )
    repo.claim_ticket(conn, live_external["code"], agent="opencode")
    repo.link_ticket(
        conn, live_external["code"], "external", "dotfiles@abc1234"
    )
    external_done = repo.complete_ticket(
        conn, live_external["code"], resolution="con external"
    )
    live_commit = repo.create_ticket(
        conn, title="Live con commit", isolation="none"
    )
    repo.claim_ticket(conn, live_commit["code"], agent="opencode")
    commit_done = repo.complete_ticket(
        conn, live_commit["code"], resolution="con commit", commit="abc1234"
    )
    h.ok(
        external_done["status"] == "completado"
        and any(
            item["kind"] == "external" and item["ref"] == "dotfiles@abc1234"
            for item in external_done["links"]
        )
        and commit_done["status"] == "completado",
        "complete live pasa con external repo@sha o con commit",
    )

    config = workspace.default_config(h.tmp)
    h.eq(
        workspace.isolation_settings(config),
        {"default": "worktree", "live_branch": "main"},
        "la configuración trae isolation por defecto segura",
    )
    h.eq(
        workspace.scope_roots(
            config, {"scope": {"roots": ["/srv/taiki"]}}
        ),
        [".", "/srv/taiki"],
        "scope roots combina config.json y local.json",
    )
    h.raises(
        domain.ValidationError,
        lambda: workspace.isolation_settings(
            {"isolation": {"default": "docker"}}
        ),
        "la configuración rechaza un isolation inválido",
    )
