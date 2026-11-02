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
        == ["pitfall", "convention"],
        "el catálogo expone memory_checklist como herramienta de lectura",
    )

    for _ in range(5):
        _closed(h.conn, profile="worker-high", paths=["src/a/"])
    agents = handlers.call_tool("agents", {"kind": "bug"})
    h.ok(
        agents["advice"]["profile"] == "worker-high"
        and agents["advice"]["sample"] == 5
        and agents["recommended"]["name"] == "worker-high",
        "agents incluye advice observado sin cambiar el routing recomendado",
    )
    h.eq(
        handlers.call_tool("agents", {})["advice"],
        None,
        "agents sin tipo devuelve advice nulo",
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
    h.eq(
        by_paths["count"],
        1,
        "memory_checklist respeta el límite y las rutas",
    )
    h.eq(
        by_paths["checklist"][0]["code"],
        pitfall["code"],
        "memory_checklist prioriza el pitfall fijado por confianza",
    )
    by_kind = handlers.call_tool(
        "memory_checklist",
        {"kind": "convention", "paths": ["ticketlib/memory/advice.py"]},
    )
    h.ok(
        by_kind["count"] == 1
        and by_kind["checklist"][0]["kind"] == "convention",
        "memory_checklist filtra por tipo de memoria",
    )
    h.ok(
        _raises(
            lambda: handlers.call_tool(
                "memory_checklist", {"kind": "bug"}
            )
        )
        and _raises(
            lambda: handlers.call_tool("memory_checklist", {"limit": 0})
        ),
        "memory_checklist rechaza tipo y límite inválidos",
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
