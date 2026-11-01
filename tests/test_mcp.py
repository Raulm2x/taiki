"""Handlers MCP, sesiones y código desactualizado."""

from __future__ import annotations

import json
import os
import server as mcp_server
import tui
from mcp_layer import errors, handlers, schemas
from ticketlib import audit as audit_lib, db, domain, repo, runtime
from tests.support import Harness


def run(h: Harness) -> None:
    conn = h.conn
    tmp = str(h.tmp)

    # ── 5. MCP Handlers & Session Resolution Suite ───────────────────────
    # Validación de registro TOOL_DISPATCH
    h.ok(isinstance(handlers.TOOL_DISPATCH, dict), "TOOL_DISPATCH debe ser un dict")
    expected_tools = [
        "create", "list", "get", "claim", "update",
        "transition", "complete", "comment", "link", "stats", "next"
    ]
    h.ok(
        all(t in handlers.TOOL_DISPATCH for t in expected_tools),
        "TOOL_DISPATCH debe contener las 11 herramientas principales",
    )

    # Precedencia en resolve_session
    # 1. meta > args > env
    sess_meta = handlers.resolve_session(
        meta={"sessionId": "meta-session-uuid"},
        args={"sessionId": "args-session-uuid"},
    )
    h.ok(sess_meta == "meta-session-uuid", "meta['sessionId'] tiene precedencia sobre args")

    # Claves alternativas en meta
    h.ok(
        handlers.resolve_session(meta={"session_id": "meta-snake"}) == "meta-snake",
        "resolve_session admite 'session_id' en meta",
    )
    h.ok(
        handlers.resolve_session(meta={"ai.opencode/sessionID": "legacy-opencode"}) == "legacy-opencode",
        "resolve_session admite 'ai.opencode/sessionID' en meta",
    )

    # 2. args > env
    isolated = (
        "ANTIGRAVITY_CONVERSATION_ID",
        "AGY_SESSION_ID",
        "SESSION_ID",
        "OPENCODE_SESSION_ID",
    )
    saved = {name: os.environ.pop(name, None) for name in isolated}
    try:
        os.environ["AGY_SESSION_ID"] = "env-agy-sess"
        sess_arg = handlers.resolve_session(
            meta=None,
            args={"session_id": "args-session-override"},
        )
        h.ok(sess_arg == "args-session-override", "args tiene precedencia sobre variables de entorno")

        # 3. Fallback a entorno (ANTIGRAVITY_CONVERSATION_ID > AGY_SESSION_ID > SESSION_ID)
        os.environ["ANTIGRAVITY_CONVERSATION_ID"] = "env-antigravity-conv"
        h.ok(
            handlers.resolve_session() == "env-antigravity-conv",
            "resolve_session cae a ANTIGRAVITY_CONVERSATION_ID en entorno",
        )
        os.environ.pop("ANTIGRAVITY_CONVERSATION_ID", None)

        sess_env_agy = handlers.resolve_session()
        h.ok(sess_env_agy == "env-agy-sess", "resolve_session cae a AGY_SESSION_ID en entorno")
        os.environ.pop("AGY_SESSION_ID", None)

        os.environ["SESSION_ID"] = "env-std-sess"
        sess_env_std = handlers.resolve_session()
        h.ok(sess_env_std == "env-std-sess", "resolve_session cae a SESSION_ID si AGY_SESSION_ID no está")
        os.environ.pop("SESSION_ID", None)

        # 4. Sin meta, args ni env -> None
        h.ok(
            handlers.resolve_session(meta={}, args={}) is None,
            "sin datos de sesión devuelve None",
        )
    finally:
        for name, value in saved.items():
            if value is not None:
                os.environ[name] = value

    # 5. Normalización de espacios y fallback ante valores vacíos
    h.ok(
        handlers.resolve_session(meta={"sessionId": "   trimmed-uuid   "}) == "trimmed-uuid",
        "resolve_session recorta espacios en blanco en la sesión",
    )
    h.ok(
        handlers.resolve_session(meta={"sessionId": "   "}, args={"session_id": "fallback-arg"}) == "fallback-arg",
        "meta vacío cae al identificador provisto en args",
    )

    # ── Validación de argumentos y errores estructurados ───────────────
    h.raises(
        domain.ValidationError,
        lambda: handlers.call_tool("list", {"statuss": ["pendiente"]}),
        "un argumento desconocido se rechaza en vez de ignorarse",
    )
    h.raises(
        domain.ValidationError,
        lambda: handlers.call_tool("list", {"limit": "dos"}),
        "un tipo incorrecto se rechaza",
    )
    h.raises(
        domain.ValidationError,
        lambda: handlers.call_tool(
            "get", {"code": "T-0001", "include": ["inventada"]}
        ),
        "un valor fuera del enum se rechaza",
    )
    h.raises(
        domain.ValidationError,
        lambda: handlers.call_tool("create", {"title": "x", "parent_id": 3}),
        "un tipo incorrecto en un campo opcional se rechaza",
    )
    h.raises(
        domain.ValidationError,
        lambda: handlers.call_tool("taiki_list", {"statuss": []}),
        "la validación también cubre los alias con prefijo",
    )
    try:
        handlers.call_tool("get", {"code": "T-9999"})
    except domain.NotFoundError as exc:
        error = errors.payload(exc, tool="get")
        h.eq(
            error["error"]["code"],
            "not_found",
            "errors.payload clasifica un ticket inexistente",
        )
        h.ok(
            error["error"].get("hint"),
            "el error incluye una pista accionable",
        )
    stale = errors.payload(
        domain.ValidationError("El MCP está desactualizado: recarga")
    )
    h.eq(
        stale["error"]["code"],
        "stale_server",
        "errors.payload distingue un MCP desactualizado",
    )
    envelope = mcp_server._tool_error(
        errors.payload(domain.ValidationError("argumento malo"), tool="list")
    )
    h.ok(
        envelope["isError"]
        and envelope["structuredContent"]["error"]["code"]
        == "invalid_arguments",
        "el servidor devuelve errores estructurados con isError",
    )
    tool_result = mcp_server._tool_result({"a": 1, "b": [1, 2]})
    h.ok(
        "structuredContent" not in tool_result
        and json.loads(tool_result["content"][0]["text"]) == {"a": 1, "b": [1, 2]},
        "las respuestas MCP usan solo JSON compacto",
    )
    by_name = {tool["name"]: tool for tool in schemas.TOOLS}
    h.ok(
        all("outputSchema" not in tool for tool in schemas.TOOLS),
        "las herramientas no declaran un outputSchema genérico",
    )
    h.ok(
        by_name["get"].get("annotations", {}).get("readOnlyHint")
        and not by_name["create"].get("annotations", {}).get(
            "readOnlyHint", False
        ),
        "solo las herramientas de lectura se marcan como read-only",
    )

    # ── Límites de tamaño y bloqueo global de escrituras con MCP viejo ──
    h.raises(
        domain.ValidationError,
        lambda: handlers.call_tool("create", {"title": "x" * 201}),
        "un título demasiado largo se rechaza",
    )
    h.raises(
        domain.ValidationError,
        lambda: handlers.call_tool(
            "create", {"title": "ok", "body": "x" * 100_001}
        ),
        "un body demasiado largo se rechaza",
    )
    h.raises(
        domain.ValidationError,
        lambda: handlers.call_tool("list", {"query": "x" * 501}),
        "una búsqueda demasiado larga se rechaza",
    )
    h.raises(
        domain.ValidationError,
        lambda: handlers.call_tool(
            "complete",
            {
                "code": "T-0001",
                "resolution": "x",
                "files": ["f"] * 501,
            },
        ),
        "demasiados archivos en complete se rechazan",
    )
    original_stale = runtime.is_stale
    runtime.is_stale = lambda: True
    try:
        h.raises(
            domain.ValidationError,
            lambda: handlers.call_tool("create", {"title": "MCP viejo"}),
            "un MCP viejo bloquea cualquier escritura, no solo las subtareas",
        )
        h.ok(
            handlers.call_tool("list", {"limit": 1})["count"] >= 0,
            "un MCP viejo sigue permitiendo lecturas",
        )
    finally:
        runtime.is_stale = original_stale

    # ── Recursos acotados y versión derivada ────────────────────────────
    inbox = handlers.read_resource("taiki://inbox")
    payload = json.loads(inbox["contents"][0]["text"])
    h.ok(
        {"count", "total", "truncated", "tickets"} <= set(payload)
        and all("body" not in item for item in payload["tickets"]),
        "el recurso inbox devuelve resumen y avisa de truncado",
    )
    h.ok(
        mcp_server.SERVER_VERSION
        and mcp_server.SERVER_VERSION != "1.0.0",
        "la versión del servidor se deriva del código",
    )
    original_register = mcp_server._register_runtime
    mcp_server._register_runtime = lambda: None
    try:
        initialized = mcp_server._initialize(
            {"protocolVersion": "2025-06-18", "clientInfo": {"name": "test"}}
        )
    finally:
        mcp_server._register_runtime = original_register
        handlers.set_client_info(None)
    h.ok(
        "logging" not in initialized["capabilities"],
        "no se anuncia una capacidad de logging sin notificaciones",
    )

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
    h.raises(
        domain.TransitionError,
        lambda: handlers.call_tool(
            "claim",
            {"code": mcp_created["code"], "agent": "agente:mcp_runner"},
        ),
        "otro agente no puede reclamar un ticket ya asignado al crearlo",
    )

    # Comprobar que el evento de creación guardó la sesión
    mcp_detail = repo.get_ticket(conn, mcp_created["code"])
    h.ok(len(mcp_detail["events"]) >= 1, "el ticket creado vía MCP tiene eventos registrados")
    h.ok(
        any(e.get("session_id") == "session-audit-999" for e in mcp_detail["events"]),
        "session_id se almacena correctamente en la tabla events",
    )

    # call_tool('list') con filtro tag
    mcp_list = handlers.call_tool("list", {"tag": "mcp-suite"})
    h.ok(mcp_list["count"] >= 1, "call_tool('list') con filtro tag devuelve al menos 1 resultado")
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
    completed_full = handlers.call_tool(
        "get", {"code": mcp_created["code"]}
    )
    h.ok(
        any(
            event.get("note") == completed_full.get("resolution")
            for event in completed_full["events"]
        ),
        "get conserva el note del evento de cierre",
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
    mcp_next = handlers.call_tool("next")
    h.ok("ticket" in mcp_next, "call_tool('next') devuelve clave 'ticket'")

    # Despacho por alias con prefijo tickets_ (compatibilidad de namespace)
    taiki_alias = handlers.call_tool("taiki_stats")
    h.ok("total" in taiki_alias, "call_tool('taiki_stats') despacha mediante alias")
    alias_stats = handlers.call_tool("tickets_stats")
    h.ok("total" in alias_stats, "call_tool('tickets_stats') despacha correctamente mediante alias")

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

    # ── Aviso de MCP desactualizado y huecos ────────────────────
    db.meta_set(conn, "mcp_fingerprint", "viejo0000000")
    stale = runtime.status(conn)
    h.ok(
        stale["stale"] and stale["stored"] == "viejo0000000",
        "runtime detecta un MCP con código viejo",
    )
    db.meta_set(conn, "mcp_fingerprint", runtime.code_fingerprint())
    h.ok(
        not runtime.status(conn)["stale"],
        "runtime reconoce el código actual",
    )
    empty = db.connect(":memory:")
    try:
        h.ok(
            audit_lib.code_gaps(empty) == [],
            "sin tickets no hay huecos",
        )
        for title in ("Uno", "Dos", "Tres"):
            repo.create_ticket(empty, title=title)
        renamed = repo.get_ticket(empty, "T-0003")
        empty.execute(
            "UPDATE tickets SET code = 'T-0002.01' WHERE id = ?",
            (renamed["id"],),
        )
        from ticketlib.store.common import event as _event

        _event(
            empty,
            renamed["id"],
            "renumerar",
            "test",
            "agente",
            None,
            renamed["status"],
            renamed["status"],
            "T-0003 → T-0002.01",
        )
        db.meta_set(empty, "next_ticket_number", "6")
        gaps = audit_lib.code_gaps(empty)
        h.ok(
            {item["code"]: item["reason"] for item in gaps}
            == {
                "T-0003": "renumerado a T-0002.01",
                "T-0004": "sin rastro",
                "T-0005": "sin rastro",
            },
            "audit explica los huecos de numeración",
        )
    finally:
        empty.close()

    tracked = {
        path.relative_to(runtime.ROOT).as_posix()
        for path in runtime._tracked_files()
    }
    h.ok(
        "ticketlib/guard/policy.py" not in tracked
        and "ticketlib/sessions/backends.py" in tracked,
        "runtime excluye el guard del MCP y vigila el resto del núcleo",
    )

    mcp_state = tui.TuiState()
    mcp_state.load([], stats={}, mcp_stale=True, mcp_orphans=2)
    h.ok(
        mcp_state.mcp_stale and mcp_state.mcp_orphans == 2,
        "la TUI recibe los avisos de MCP viejo y huérfano",
    )
