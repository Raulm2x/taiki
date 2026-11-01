"""Concurrencia, transacciones y jerarquía de subtareas."""

from __future__ import annotations

from ticketlib import db, domain, render, repo
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
    h.ok(db.meta_get(conn, "reentrant_l1") == "1", "nivel 1 de tx reentrante persistió")
    h.ok(db.meta_get(conn, "reentrant_l2") == "2", "nivel 2 de tx reentrante persistió")
    h.ok(db.meta_get(conn, "reentrant_l3") == "3", "nivel 3 de tx reentrante persistió")

    # Reversión completa (ROLLBACK) al lanzar excepción en tx anidada
    try:
        with db.tx(conn):
            conn.execute("INSERT INTO meta(key, value) VALUES ('rollback_outer', 'outer')")
            with db.tx(conn):
                conn.execute("INSERT INTO meta(key, value) VALUES ('rollback_inner', 'inner')")
                raise RuntimeError("Fallo provocado en tx anidada")
    except RuntimeError:
        pass
    h.ok(db.meta_get(conn, "rollback_outer") is None, "excepción en tx anidada hace rollback de outer")
    h.ok(db.meta_get(conn, "rollback_inner") is None, "excepción en tx anidada hace rollback de inner")

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
