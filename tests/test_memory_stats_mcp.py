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
    ledger_after = conn.execute("SELECT COUNT(*) FROM memory_ledger").fetchone()[0]
    stats = payload["stats"]
    h.ok(
        set(payload) == {"stats", "impact"}
        and stats["by_status"]["active"] >= 1
        and stats["by_status"]["proposed"] == 1
        and proposal["code"] in stats["proposals"]["codes"]
        and {"project", "user"} <= set(stats["budget"])
        and payload["impact"]["closed"] >= 1
        and payload["impact"]["notes"]
        and ledger_after == ledger_before,
        "mode=all devuelve stats, presupuesto e impacto sin escribir el ledger",
    )

    only_stats = handlers.call_tool("memory_stats", {"mode": "stats"}, meta=_META)
    only_impact = handlers.call_tool(
        "memory_stats", {"mode": "impact", "days": 7}, meta=_META
    )
    h.ok(
        set(only_stats) == {"stats"}
        and set(only_impact) == {"impact"}
        and only_impact["impact"]["days"] == 7,
        "mode=stats e impact se limitan a su bloque y respetan days",
    )

    h.raises(
        ValidationError,
        lambda: handlers.call_tool(
            "memory_stats", {"mode": "otro"}, meta=_META
        ),
        "mode desconocido se rechaza",
    )
    h.raises(
        ValidationError,
        lambda: handlers.call_tool(
            "memory_stats", {"mode": "impact", "days": 0}, meta=_META
        ),
        "days=0 se rechaza antes de ejecutar",
    )
