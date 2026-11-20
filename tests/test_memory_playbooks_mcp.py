"""Contrato MCP de playbooks: tool, escritura, recurso y prompt."""

from __future__ import annotations

import json

from mcp_layer import handlers, schemas
from ticketlib import domain, repo
from ticketlib.memory import create_playbook
from tests.support import Harness


def _tool(name: str) -> dict:
    return next(tool for tool in schemas.TOOLS if tool["name"] == name)


def _raises(func) -> bool:
    try:
        func()
    except domain.ValidationError:
        return True
    return False


def run(h: Harness) -> None:
    playbook_tool = _tool("memory_playbook")
    write_tool = _tool("memory_write")
    operation = write_tool["inputSchema"]["properties"]["operations"]["items"]
    h.ok(
        "memory_playbook" in {tool["name"] for tool in schemas.TOOLS}
        and playbook_tool["annotations"] == {"readOnlyHint": True}
        and "memory_playbook" not in handlers.MUTATING_TOOLS
        and "memory_playbook" in handlers.TOOL_DISPATCH
        and playbook_tool["inputSchema"]["required"] == ["slug"]
        and {"path"} <= set(playbook_tool["inputSchema"]["properties"])
        and {"files", "description"} <= set(operation["properties"])
        and "playbook" in operation["properties"]["kind"]["enum"],
        "el catálogo expone memory_playbook y memory_write acepta playbooks",
    )

    ticket = repo.create_ticket(h.conn, title="Playbooks MCP", kind="funcionalidad")
    created = create_playbook(
        h.conn,
        slug="revisar-pr",
        title="Revisar PR",
        description="Checklist para revisar un pull request.",
        content="1. Lee el diff.\n2. Ejecuta los gates.",
        evidence=[{"kind": "ticket", "ref": ticket["code"]}],
        files=[
            {"path": "references/check.md", "content": "- tests\n"},
            {"path": "scripts/run.sh", "content": "#!/bin/sh\necho ok\n"},
        ],
        source="user",
        actor="usuario",
    )

    loaded = handlers.call_tool("memory_playbook", {"slug": "revisar-pr"})
    h.ok(
        loaded["code"] == created["code"]
        and loaded["slug"] == "revisar-pr"
        and loaded["description"] == "Checklist para revisar un pull request."
        and "Lee el diff" in loaded["content"]
        and loaded["evidence"][0]["ref"] == ticket["code"]
        and [item["path"] for item in loaded["files"]]
        == ["references/check.md", "scripts/run.sh"]
        and all("content" not in item for item in loaded["files"]),
        "memory_playbook resuelve por slug con metadatos, cuerpo y apoyos compactos",
    )

    support = handlers.call_tool(
        "memory_playbook",
        {"slug": "revisar-pr", "path": "scripts/run.sh"},
    )
    h.eq(
        support["support"],
        {"path": "scripts/run.sh", "content": "#!/bin/sh\necho ok\n"},
        "path carga un apoyo concreto",
    )
    h.ok(
        _raises(
            lambda: handlers.call_tool(
                "memory_playbook",
                {"slug": "revisar-pr", "path": "references/nope.md"},
            )
        )
        and _raises(
            lambda: handlers.call_tool(
                "memory_playbook", {"slug": "revisar-pr", "path": "../fuera.md"}
            )
        )
        and _raises(lambda: handlers.call_tool("memory_playbook", {"slug": "nope"}))
        and _raises(lambda: handlers.call_tool("memory_playbook", {})),
        "apoyos inexistentes, traversal, slug desconocido y slug ausente fallan",
    )

    resource = handlers.read_resource("taiki://playbook/revisar-pr")
    resource_data = json.loads(resource["contents"][0]["text"])
    h.ok(
        resource["contents"][0]["mimeType"] == "application/json"
        and resource_data["code"] == created["code"]
        and any(
            item["uriTemplate"] == "taiki://playbook/{slug}"
            for item in schemas.RESOURCE_TEMPLATES
        ),
        "el recurso taiki://playbook/{slug} devuelve el playbook",
    )
    prompt = handlers.get_prompt("taiki:playbook", {"slug": "revisar-pr"})
    prompt_text = prompt["messages"][0]["content"]["text"]
    h.ok(
        "Revisar PR" in prompt_text
        and "Lee el diff" in prompt_text
        and "pitfall" in prompt_text
        and "no se ejecutan automáticamente" in prompt_text
        and any(item["name"] == "taiki:playbook" for item in schemas.PROMPTS),
        "el prompt taiki:playbook carga el procedimiento y avisa de pitfalls",
    )

    long_content = "Paso de playbook. " * 40
    written = handlers.call_tool(
        "memory_write",
        {
            "operations": [
                {
                    "action": "add",
                    "kind": "playbook",
                    "key": "playbook:desde-mcp",
                    "title": "Desde MCP",
                    "description": "Playbook creado por memory_write.",
                    "content": long_content,
                    "files": [
                        {"path": "references/guia.md", "content": "guía\n"}
                    ],
                    "evidence": [ticket["code"]],
                    "confidence": 0.9,
                }
            ]
        },
    )
    mcp_playbook = handlers.call_tool("memory_playbook", {"slug": "desde-mcp"})
    h.ok(
        written["proposed"] == 1
        and mcp_playbook["status"] == "proposed"
        and len(mcp_playbook["content"]) > 600
        and mcp_playbook["files"][0]["path"] == "references/guia.md",
        "memory_write crea playbooks largos con description y files",
    )

    patched = handlers.call_tool(
        "memory_write",
        {
            "operations": [
                {
                    "action": "patch",
                    "code": written["ids"][0],
                    "description": "Descripción corregida.",
                    "content": "Cuerpo corregido.",
                    "files": [
                        {"path": "templates/plan.md", "content": "plan\n"}
                    ],
                }
            ]
        },
    )
    updated = handlers.call_tool("memory_playbook", {"slug": "desde-mcp"})
    h.ok(
        patched["proposed"] == 1
        and updated["description"] == "Descripción corregida."
        and updated["content"] == "Cuerpo corregido."
        and [item["path"] for item in updated["files"]] == ["templates/plan.md"],
        "memory_write patch actualiza description, content y files",
    )

    invalid = (
        lambda: handlers.call_tool(
            "memory_write",
            {
                "operations": [
                    {
                        "action": "add",
                        "kind": "playbook",
                        "title": "Sin clave",
                        "description": "x",
                        "content": "y",
                        "evidence": [ticket["code"]],
                    }
                ]
            },
        ),
        lambda: handlers.call_tool(
            "memory_write",
            {
                "operations": [
                    {
                        "action": "add",
                        "kind": "playbook",
                        "key": "playbook:traversal",
                        "title": "Traversal",
                        "description": "x",
                        "content": "y",
                        "files": [{"path": "../fuera.md", "content": "x"}],
                        "evidence": [ticket["code"]],
                    }
                ]
            },
        ),
        lambda: handlers.call_tool(
            "memory_write",
            {
                "operations": [
                    {
                        "action": "add",
                        "kind": "playbook",
                        "key": "playbook:campo",
                        "title": "Campo",
                        "description": "x",
                        "content": "y",
                        "files": [{"path": "references/x.md", "content": "x", "nope": 1}],
                        "evidence": [ticket["code"]],
                    }
                ]
            },
        ),
    )
    h.ok(
        all(_raises(func) for func in invalid),
        "memory_write rechaza playbooks sin clave, con traversal y campos muertos",
    )
