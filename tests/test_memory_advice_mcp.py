"""Contrato MCP de advice: agents/schedule, memory_checklist y catálogo."""

from __future__ import annotations

from mcp_layer import handlers, memory as memory_handlers, schemas
from ticketlib import domain, repo
from ticketlib.memory import add_memory
from tests.support import Harness


def _tool(name: str) -> dict:
    return next(tool for tool in schemas.TOOLS if tool["name"] == name)


def _raises(func) -> bool:
    try:
        func()
    except domain.ValidationError:
        return True
    return False


def _closed(conn, *, profile: str, paths: list[str], iterations: int = 0) -> None:
    ticket = repo.create_ticket(
        conn,
        title=f"Bug cerrado {profile}",
        kind="bug",
        paths=paths,
        project="advice",
    )
    conn.execute(
        "UPDATE tickets SET status = 'confirmado', assigned_to = ?, iterations = ? "
        "WHERE id = ?",
        (profile, iterations, ticket["id"]),
    )


def run(h: Harness) -> None:
    checklist_tool = _tool("memory_checklist")
    h.ok(
        "memory_checklist" in {tool["name"] for tool in schemas.TOOLS}
        and checklist_tool["annotations"] == {"readOnlyHint": True}
        and "memory_checklist" in handlers.TOOL_DISPATCH
        and "memory_checklist" not in handlers.MUTATING_TOOLS
        and checklist_tool["inputSchema"]["properties"]["kind"]["enum"]
        == list(domain.KINDS)
        and checklist_tool["inputSchema"]["properties"]["memory_kind"]["enum"]
        == ["pitfall", "convention"],
        "el catálogo expone memory_checklist con tipo de ticket y de memoria",
    )

    for _ in range(5):
        _closed(h.conn, profile="worker-high", paths=["src/a/"])
    agents = handlers.call_tool("agents", {"kind": "bug"})
    h.ok(
        agents["advice"]["profile"] == "worker-high"
        and agents["advice"]["sample"] == 5
        and agents["recommended"]["name"] == "worker-high"
        and handlers.call_tool("agents", {})["advice"] is None,
        "agents incluye advice observado por tipo y lo omite sin tipo",
    )

    ready = repo.create_ticket(
        h.conn,
        title="Bug listo",
        kind="bug",
        paths=["src/a/x.py"],
        project="advice-mcp",
    )
    plan = handlers.call_tool("schedule", {"project": "advice-mcp"})
    h.ok(
        plan["advice"][ready["code"]]["profile"] == "worker-high"
        and set(plan["advice"]) <= {
            *plan["parallel"],
            *(item["code"] for item in plan["serial"]),
            *plan["deferred"],
        },
        "schedule adjunta advice por cada tarea lista",
    )

    pitfall = add_memory(
        h.conn,
        title="Pitfall de advice",
        description="La muestra mínima son 5 tickets.",
        content="No inventes advice con muestras pequeñas.",
        kind="pitfall",
        ticket_kinds=["bug"],
        paths=["ticketlib/memory/advice.py"],
        source="user",
        evidence=[{"kind": "ticket", "ref": "T-0047.04"}],
    )
    add_memory(
        h.conn,
        title="Convención de tests",
        description="Un test por comportamiento.",
        content="No dupliques comprobaciones entre capas.",
        kind="convention",
        paths=["ticketlib/memory/advice.py"],
        source="user",
    )
    by_paths = handlers.call_tool(
        "memory_checklist",
        {"paths": ["ticketlib/memory/advice.py"], "limit": 1},
    )
    by_kind = handlers.call_tool(
        "memory_checklist",
        {
            "kind": "bug",
            "memory_kind": "convention",
            "paths": ["ticketlib/memory/advice.py"],
        },
    )
    by_ticket = handlers.call_tool(
        "memory_checklist",
        {"kind": "funcionalidad", "paths": ["ticketlib/memory/advice.py"]},
    )
    h.ok(
        by_paths["count"] == 1
        and by_paths["checklist"][0]["code"] == pitfall["code"]
        and by_kind["count"] == 1
        and by_kind["checklist"][0]["kind"] == "convention"
        and by_ticket["count"] == 1
        and by_ticket["checklist"][0]["kind"] == "convention",
        "memory_checklist combina rutas, límite, tipo de ticket y de memoria",
    )
    h.ok(
        _raises(
            lambda: handlers.call_tool(
                "memory_checklist", {"kind": "inventado"}
            )
        )
        and _raises(
            lambda: handlers.call_tool(
                "memory_checklist", {"memory_kind": "bug"}
            )
        )
        and _raises(
            lambda: handlers.call_tool("memory_checklist", {"limit": 0})
        ),
        "memory_checklist rechaza tipos y límite inválidos",
    )

    original = memory_handlers._config
    memory_handlers._config = lambda: {"enabled": False}
    try:
        disabled = handlers.call_tool(
            "memory_checklist", {"paths": ["ticketlib/memory/advice.py"]}
        )
    finally:
        memory_handlers._config = original
    h.eq(disabled["count"], 0, "la memoria desactivada devuelve checklist vacío")
