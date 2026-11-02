"""Contrato MCP de memory_stats: solo lectura, modos y ventana de impacto."""

from __future__ import annotations

from mcp_layer import handlers, schemas
from ticketlib import repo
from ticketlib.domain import ValidationError
from ticketlib.memory import add_memory, get_memory
from tests.support import Harness

_META = {"sessionId": "ses_memory_stats"}


def run(h: Harness) -> None:
    conn = h.conn
    active = add_memory(
        conn,
        title="Activa MCP stats",
        content="Contenido activo.",
        key="mcp-stats-active",
        source="user",
    )
    proposal = add_memory(
        conn,
        title="Propuesta MCP stats",
        content="Pendiente.",
        kind="decision",
        key="mcp-stats-proposal",
        source="agent",
        evidence=[{"kind": "ticket", "ref": "T-0048.03"}],
    )
    get_memory(conn, active["code"], session_id=_META["sessionId"], record_usage=True)
    ticket = repo.create_ticket(conn, title="Impacto MCP", kind="bug")
    repo.claim_ticket(
        conn, ticket["code"], agent="worker-mcp", session_id="ses_mcp_ticket"
    )
    repo.complete_ticket(
        conn,
        ticket["code"],
        resolution="ok",
        actor="worker-mcp",
        session_id="ses_mcp_ticket",
    )

    tool = next(item for item in schemas.TOOLS if item["name"] == "memory_stats")
    h.ok(
        (tool.get("annotations") or {}).get("readOnlyHint") is True
        and "memory_stats" not in handlers.MUTATING_TOOLS,
        "memory_stats se anuncia como solo lectura",
    )

    ledger_before = conn.execute("SELECT COUNT(*) FROM memory_ledger").fetchone()[0]
    payload = handlers.call_tool("memory_stats", {}, meta=_META)
    h.eq(
        set(payload),
        {"stats", "impact"},
        "mode=all devuelve stats e impact",
    )
    stats = payload["stats"]
    h.ok(
        stats["by_status"]["active"] >= 1
        and stats["by_status"]["proposed"] == 1
        and proposal["code"] in stats["proposals"]["codes"],
        "stats reutiliza conteos y propuestas",
    )
    h.ok(
        {"project", "user"} <= set(stats["budget"]),
        "memory_stats expone presupuesto por scope",
    )
    h.ok(
        payload["impact"]["closed"] >= 1
        and payload["impact"]["notes"],
        "impact resume cierres y recuerda la no causalidad",
    )
    ledger_after = conn.execute("SELECT COUNT(*) FROM memory_ledger").fetchone()[0]
    h.eq(
        ledger_after,
        ledger_before,
        "memory_stats no escribe en el ledger",
    )

    only_stats = handlers.call_tool("memory_stats", {"mode": "stats"}, meta=_META)
    h.eq(set(only_stats), {"stats"}, "mode=stats no calcula impacto")
    only_impact = handlers.call_tool(
        "memory_stats", {"mode": "impact", "days": 7}, meta=_META
    )
    h.eq(set(only_impact), {"impact"}, "mode=impact no calcula stats")
    h.eq(only_impact["impact"]["days"], 7, "days se pasa al impacto")

    h.raises(
        ValidationError,
        lambda: handlers.call_tool(
            "memory_stats", {"mode": "impact", "days": 0}, meta=_META
        ),
        "days=0 se rechaza antes de ejecutar",
    )
    h.raises(
        ValidationError,
        lambda: handlers.call_tool("memory_stats", {"mode": "otro"}, meta=_META),
        "mode desconocido se rechaza",
    )
