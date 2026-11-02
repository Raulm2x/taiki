"""Despacho de herramientas MCP: contrato de create/list/get/complete."""

from __future__ import annotations

from mcp_layer import handlers, schemas
from ticketlib import domain, repo
from tests.support import Harness


def run(h: Harness) -> None:
    conn = h.conn
    by_name = {tool["name"]: tool for tool in schemas.TOOLS}
    # Despacho de herramientas vía call_tool y registro de eventos
    mcp_created = handlers.call_tool(
        "create",
        {
            "title": "Ticket creado mediante protocolo MCP",
            "body": "Verificando ejecución de handlers.",
            "kind": "funcionalidad",
            "priority": "alta",
            "tags": ["mcp-suite", "integration"],
            "agent": "mcp:tester",
        },
        meta={"sessionId": "session-audit-999"},
    )
    h.ok(mcp_created["code"].startswith("T-"), "call_tool('create') genera código secuencial válido")
    h.ok(mcp_created["title"] == "Ticket creado mediante protocolo MCP", "título de ticket MCP coincide")
    h.ok(
        mcp_created["assigned_to"] == "mcp:tester",
        "el ticket creado vía MCP queda asignado a su agente",
    )
    batch = handlers.call_tool(
        "create",
        {
            "title": "Épica en lote",
            "subtasks": ["Primera", "Segunda"],
            "idempotency_key": "lote-1",
            "agent": "mcp:tester",
        },
    )
    h.eq(
        len(batch["subtasks"]),
        2,
        "create con subtasks crea las hijas en una llamada",
    )
    replay = handlers.call_tool(
        "create",
        {
            "title": "Épica en lote",
            "subtasks": ["Primera", "Segunda"],
            "idempotency_key": "lote-1",
            "agent": "mcp:tester",
        },
    )
    h.eq(
        replay["code"],
        batch["code"],
        "idempotency_key devuelve el mismo ticket en un reintento",
    )
    h.ok(
        replay.get("idempotent_replay"),
        "el reintento se marca como replay",
    )
    h.raises(
        domain.ValidationError,
        lambda: handlers.call_tool(
            "create", {"title": "Vacía", "subtasks": [""]}
        ),
        "una subtarea sin título se rechaza",
    )

    handlers.set_client_info({"name": "mcp-client", "version": "1.0"})
    try:
        from_client = handlers.call_tool(
            "create", {"title": "Sin agent explícito"}
        )
        h.eq(
            from_client["assigned_to"],
            "mcp-client",
            "sin agent se usa el nombre del cliente MCP",
        )
    finally:
        handlers.set_client_info(None)
    # ``assigned_to`` es informativo: el claim ajeno se permite sin owner y
    # solo se rechaza cuando ya hay un dueño con lease vigente.
    def claim_ajeno(agent: str) -> dict[str, object] | bool:
        try:
            return handlers.call_tool(
                "claim", {"code": from_client["code"], "agent": agent}
            )
        except domain.TransitionError:
            return True

    first_claim = claim_ajeno("agente:mcp_runner")
    h.ok(
        isinstance(first_claim, dict)
        and first_claim["owner_session"] == "agent:agente:mcp_runner"
        and claim_ajeno("agente:otro") is True,
        "claim ajeno sin owner se permite; con owner vigente se rechaza",
    )

    # Comprobar que el evento de creación guardó la sesión
    mcp_detail = repo.get_ticket(conn, mcp_created["code"])
    h.ok(
        any(e.get("session_id") == "session-audit-999" for e in mcp_detail["events"]),
        "session_id se almacena correctamente en la tabla events",
    )

    # call_tool('list') con filtro tag
    mcp_list = handlers.call_tool("list", {"tag": "mcp-suite"})
    h.ok(
        any(t["code"] == mcp_created["code"] for t in mcp_list["tickets"]),
        "el ticket creado con tag 'mcp-suite' está en el listado",
    )

    # call_tool('get')
    mcp_got = handlers.call_tool("get", {"code": mcp_created["code"]})
    h.ok(mcp_got["code"] == mcp_created["code"], "call_tool('get') recupera el ticket exacto")

    # ── Contexto: list resumido, get acotado y next resumido ────────────
    lean = handlers.call_tool("list", {"tag": "mcp-suite"})
    lean_row = next(
        item for item in lean["tickets"] if item["code"] == mcp_created["code"]
    )
    h.ok(
        "body" not in lean_row and "title" in lean_row,
        "list devuelve un resumen sin body",
    )
    h.ok(
        "owner_session" not in lean_row
        and "created_at" not in lean_row
        and "progress" not in lean_row,
        "list lean omite sesión, created_at y progreso vacío",
    )
    h.eq(
        by_name["list"]["inputSchema"]["properties"]["limit"]["default"],
        20,
        "list pagina de 20 en 20 por defecto",
    )
    full_list = handlers.call_tool(
        "list", {"tag": "mcp-suite", "detail": True}
    )
    h.ok(
        "body" in full_list["tickets"][0],
        "list con detail devuelve el registro completo",
    )
    projected = handlers.call_tool(
        "list", {"tag": "mcp-suite", "fields": ["code", "status"]}
    )
    h.ok(
        set(projected["tickets"][0]) == {"code", "status"},
        "list con fields devuelve solo los campos pedidos",
    )

    # call_tool('claim') con el mismo agente y una sesión nueva
    mcp_claimed = handlers.call_tool(
        "claim",
        {"code": mcp_created["code"], "agent": "mcp:tester"},
        meta={"sessionId": "session-claim-111"},
    )
    h.ok(mcp_claimed["status"] == "en_progreso", "call_tool('claim') cambia estado a en_progreso")
    h.ok(mcp_claimed["assigned_to"] == "mcp:tester", "call_tool('claim') asigna al agente")

    # call_tool('update')
    mcp_updated = handlers.call_tool(
        "update",
        {"code": mcp_created["code"], "priority": "urgente"},
    )
    h.ok(mcp_updated["priority"] == "urgente", "call_tool('update') modifica prioridad")
    h.ok(
        "events" not in mcp_updated and "body" not in mcp_updated,
        "update devuelve un recibo sin historial",
    )
    full_update = handlers.call_tool(
        "update",
        {"code": mcp_created["code"], "priority": "urgente", "detail": True},
        meta={"sessionId": "session-claim-111"},
    )
    h.ok(
        "events" in full_update and "body" in full_update,
        "detail=true mantiene el ticket completo",
    )

    # call_tool('comment')
    mcp_commented = handlers.call_tool(
        "comment",
        {"code": mcp_created["code"], "body": "Comentario registrado por MCP"},
    )
    h.ok(
        mcp_commented["comment"]["body"] == "Comentario registrado por MCP"
        and mcp_commented["comments_count"] == 1,
        "call_tool('comment') devuelve el comentario creado",
    )

    # call_tool('link')
    mcp_linked = handlers.call_tool(
        "link",
        {"code": mcp_created["code"], "kind": "url", "ref": "https://mcp.spec/test"},
    )
    h.ok(
        mcp_linked["link"]["ref"] == "https://mcp.spec/test"
        and mcp_linked["links_count"] == 1,
        "call_tool('link') devuelve el enlace creado",
    )

    for index in range(3):
        handlers.call_tool(
            "link",
            {
                "code": mcp_created["code"],
                "kind": "commit",
                "ref": f"commit-{index}",
            },
            meta={"sessionId": "session-claim-111"},
        )
    limited = handlers.call_tool(
        "get",
        {
            "code": mcp_created["code"],
            "links_limit": 2,
            "commits_limit": 1,
        },
    )
    h.ok(
        len(limited["links"]) == 2
        and any(link["kind"] == "commit" for link in limited["links"])
        and len(limited["commits"]) == 1
        and limited["commits_total"] == 3,
        "get conserva los enlaces commit y acota cada sección",
    )
    only_links = handlers.call_tool(
        "get",
        {
            "code": mcp_created["code"],
            "include": ["links"],
            "links_limit": 0,
        },
    )
    h.ok(
        len(only_links["links"]) == 4
        and sum(1 for link in only_links["links"] if link["kind"] == "commit") == 3,
        "include=['links'] devuelve también los enlaces commit",
    )
    limited_batch = handlers.call_tool(
        "get", {"code": batch["code"], "subtasks_limit": 1}
    )
    h.ok(
        len(limited_batch["subtasks"]) == 1
        and limited_batch["subtasks_total"] == 2,
        "get acota subtareas y conserva el total",
    )

    for index in range(5):
        handlers.call_tool(
            "comment",
            {"code": mcp_created["code"], "body": f"nota {index}"},
            meta={"sessionId": "session-claim-111"},
        )
    trimmed = handlers.call_tool(
        "get", {"code": mcp_created["code"], "history_limit": 2}
    )
    h.eq(
        len(trimmed["comments"]),
        2,
        "get con history_limit recorta los comentarios",
    )
    h.eq(
        trimmed["comments_total"],
        6,
        "get informa del total de comentarios truncados",
    )
    def _traced_get(get_args: dict) -> list[str]:
        statements: list[str] = []
        conn.set_trace_callback(statements.append)
        try:
            handlers._handle_get(conn, get_args)
        finally:
            conn.set_trace_callback(None)
        return statements

    traced = _traced_get(
        {"code": mcp_created["code"], "history_limit": 2}
    )
    h.ok(
        any(
            "FROM events" in stmt and "ORDER BY id DESC LIMIT" in stmt
            for stmt in traced
        )
        and any(
            "FROM comments" in stmt and "ORDER BY id DESC LIMIT" in stmt
            for stmt in traced
        ),
        "get aplica history_limit en SQL para eventos y comentarios",
    )
    h.ok(
        any("COUNT(*) FROM events" in stmt for stmt in traced),
        "get con recorte cuenta el total de eventos",
    )
    traced = _traced_get(
        {"code": mcp_created["code"], "history_limit": 50}
    )
    h.ok(
        not any("COUNT(*) FROM events" in stmt for stmt in traced),
        "get sin recorte no ejecuta el COUNT de eventos",
    )
    traced = _traced_get(
        {"code": mcp_created["code"], "links_limit": 2}
    )
    h.ok(
        any("FROM links" in stmt and "LIMIT" in stmt for stmt in traced),
        "get aplica links_limit en SQL",
    )
    projected = handlers.call_tool(
        "get", {"code": mcp_created["code"], "fields": ["title", "status"]}
    )
    h.eq(
        set(projected),
        {"code", "title", "status"},
        "get con fields proyecta solo los campos pedidos",
    )
    from commands.tickets import _json_summary

    sample = repo.get_ticket(conn, mcp_created["code"])
    h.eq(
        _json_summary(sample),
        handlers._summary(sample),
        "el resumen CLI y MCP comparten los mismos campos",
    )
    h.ok(
        all(
            set(event) <= {"ts", "actor", "action", "note"}
            for event in trimmed["events"]
        ),
        "get devuelve eventos slim por defecto",
    )
    detailed_events = handlers.call_tool(
        "get",
        {"code": mcp_created["code"], "events_detail": True},
    )
    h.ok(
        any(
            "session_id" in event or "actor_type" in event
            for event in detailed_events["events"]
        ),
        "events_detail=true recupera todos los campos",
    )
    only_comments = handlers.call_tool(
        "get",
        {
            "code": mcp_created["code"],
            "include": ["comments"],
            "history_limit": 0,
        },
    )
    h.ok(
        "events" not in only_comments
        and "links" not in only_comments
        and len(only_comments["comments"]) == 6,
        "get con include omite el resto de secciones",
    )

    # call_tool('complete'): otra sesión no puede cerrar el ticket
    h.raises(
        domain.PermissionDenied,
        lambda: handlers.call_tool(
            "complete",
            {
                "code": mcp_created["code"],
                "resolution": "cierre ajeno",
            },
            meta={"sessionId": "session-otra"},
        ),
        "call_tool('complete') rechaza a otra sesión",
    )
    mcp_completed = handlers.call_tool(
        "complete",
        {
            "code": mcp_created["code"],
            "resolution": "Implementado y verificado en suite MCP.",
            "commit": "mcp9876",
        },
        meta={"sessionId": "session-claim-111"},
    )
    h.ok(mcp_completed["status"] == "completado", "call_tool('complete') pasa estado a completado")
    mcp_verified = handlers.call_tool(
        "transition",
        {
            "code": mcp_created["code"],
            "status": "verificado",
            "note": "gates MCP verdes",
        },
        meta={"sessionId": "session-claim-111"},
    )
    h.ok(
        mcp_verified["status"] == "verificado",
        "call_tool('transition') verifica con evidencia",
    )
    documented = repo.create_ticket(
        conn, title="Confirmado sin resolución MCP"
    )
    conn.execute(
        "UPDATE tickets SET status = 'confirmado' WHERE code = ?",
        (documented["code"],),
    )
    conn.commit()
    mcp_documented = handlers.call_tool(
        "transition",
        {
            "code": documented["code"],
            "status": "confirmado",
            "resolution": "Cierre documentado vía MCP.",
            "detail": True,
        },
        meta={"sessionId": "session-claim-111"},
    )
    h.ok(
        mcp_documented["status"] == "confirmado"
        and mcp_documented["resolution"] == "Cierre documentado vía MCP.",
        "call_tool('transition') acepta resolution sin cambiar de estado",
    )
    review_prompt = handlers.get_prompt("review")
    h.ok(
        mcp_created["code"]
        in review_prompt["messages"][0]["content"]["text"],
        "el prompt review incluye los tickets verificados",
    )
    completed_raw = handlers.call_tool(
        "get", {"code": mcp_created["code"], "raw": True}
    )
    h.ok(
        any(
            event.get("note") == completed_raw.get("resolution")
            for event in completed_raw["events"]
        )
        and any(link["kind"] == "commit" for link in completed_raw["links"]),
        "get(raw=true) devuelve el note de cierre y los enlaces commit",
    )

    open_only = handlers.call_tool("list", {"limit": 200})
    h.ok(
        mcp_created["code"]
        not in {item["code"] for item in open_only["tickets"]},
        "list oculta los tickets cerrados por defecto",
    )
    with_closed = handlers.call_tool(
        "list", {"limit": 200, "include_closed": True}
    )
    h.ok(
        any(
            item["code"] == mcp_created["code"]
            for item in with_closed["tickets"]
        ),
        "list con include_closed muestra los cerrados",
    )

    pending = repo.create_ticket(
        conn,
        title="Siguiente MCP",
        body="x" * 300,
        project="mcp-next",
    )
    nxt = handlers.call_tool("next", {"project": "mcp-next"})
    h.ok(
        nxt["ticket"]["code"] == pending["code"]
        and "body" not in nxt["ticket"]
        and nxt["ticket"]["body_preview"].endswith("…"),
        "next devuelve un resumen con avance del body",
    )
    nxt_full = handlers.call_tool(
        "next", {"project": "mcp-next", "detail": True}
    )
    h.ok(
        "body" in nxt_full["ticket"],
        "next con detail devuelve el ticket completo",
    )

    parent = repo.create_ticket(
        conn, title="Épica MCP", project="mcp-leaves"
    )
    child = repo.create_ticket(
        conn,
        title="Tarea MCP",
        project="mcp-leaves",
        parent_id=parent["code"],
    )
    leaves = handlers.call_tool(
        "list", {"project": "mcp-leaves", "leaves_only": True}
    )
    h.eq(
        [item["code"] for item in leaves["tickets"]],
        [child["code"]],
        "list con leaves_only excluye la épica",
    )

    # call_tool('stats') y call_tool('next')
    mcp_stats = handlers.call_tool("stats")
    h.ok("total" in mcp_stats and "por_estado" in mcp_stats, "call_tool('stats') devuelve métricas")

    # Despacho por alias con prefijo tickets_ (compatibilidad de namespace)
    h.ok(
        all(
            "total" in handlers.call_tool(name)
            for name in ("taiki_stats", "tickets_stats")
        ),
        "los alias con prefijo (taiki_/tickets_) despachan a la misma herramienta",
    )

    # Herramienta desconocida lanza NotFoundError
    h.raises(
        domain.NotFoundError,
        lambda: handlers.call_tool("herramienta_no_existente"),
        "herramienta no registrada en TOOL_DISPATCH debe fallar con NotFoundError",
    )

    # Parámetros obligatorios faltantes lanzan ValidationError
    h.raises(
        domain.ValidationError,
        lambda: handlers.call_tool("get", {}),
        "call_tool('get') sin parámetro 'code' debe fallar con ValidationError",
    )
