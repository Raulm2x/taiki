"""Recursos, versión e inicialización del servidor MCP."""

from __future__ import annotations

import json

import server as mcp_server
import tui
from mcp_layer import handlers
from ticketlib import audit as audit_lib, db, repo, runtime
from tests.support import Harness


def run(h: Harness) -> None:
    conn = h.conn

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
