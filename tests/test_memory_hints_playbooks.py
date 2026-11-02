"""Playbooks en brief y hints: índice sin cuerpo, relevancia y reviewer."""

from __future__ import annotations

from pathlib import Path

from mcp_layer import memory as memory_handlers
from ticketlib.memory import add_memory, create_playbook
from ticketlib.memory.brief import render_brief
from tests.support import Harness

REVIEWER = Path(__file__).resolve().parent.parent / ".opencode/agents/reviewer.md"


def _playbook(
    conn,
    slug: str,
    *,
    title: str,
    description: str,
    content: str,
    paths: list[str] | None = None,
    source: str = "user",
):
    return create_playbook(
        conn,
        slug=slug,
        title=title,
        description=description,
        content=content,
        evidence=[{"kind": "ticket", "ref": "T-0047.05"}],
        paths=paths or [],
        source=source,
    )


def _brief(h: Harness) -> None:
    conn = h.conn
    notes = add_memory(
        conn,
        title="Nota normal",
        content="Contenido destilado.",
        kind="convention",
        source="user",
    )
    playbook = _playbook(
        conn,
        "revisar-pr",
        title="Revisar PR",
        description="Checklist para revisar un pull request.",
        content="CUERPO_SECRETO que no debe entrar en el brief.",
        paths=["src/a/"],
    )
    proposed = _playbook(
        conn,
        "propuesto",
        title="Propuesto",
        description="Todavía no aprobado.",
        content="Propuesta.",
        source="agent",
    )
    archived = _playbook(
        conn,
        "archivado",
        title="Archivado",
        description="Ya no aplica.",
        content="Archivado.",
    )
    conn.execute(
        "UPDATE memories SET status = 'archived' WHERE id = ?", (archived["id"],)
    )

    config = {
        "brief_budget_chars": 2000,
        "playbook_index_chars": 300,
    }
    result = render_brief(conn, config, budget=2000)
    text = result["text"]
    h.ok(
        "## Playbooks" in text
        and "revisar-pr" in text
        and "Revisar PR" in text
        and "Checklist para revisar un pull request." in text
        and playbook["code"] in text,
        "el brief incluye el índice compacto de playbooks",
    )
    h.ok(
        "CUERPO_SECRETO" not in text,
        "el brief no incluye el cuerpo del playbook",
    )
    h.ok(
        "propuesto" not in text and "archivado" not in text,
        "solo se indexan playbooks activos",
    )
    h.eq(
        result["sections"]["playbooks"],
        [playbook["code"]],
        "la sección playbooks expone los códigos indexados",
    )
    h.ok(
        result["usage"]["playbooks"] > 0
        and result["usage"]["used"]
        == len("Contenido destilado.") + result["usage"]["playbooks"],
        "el índice consume presupuesto y las memorias siguen contando",
    )
    h.ok(
        proposed["code"] not in result["sections"]["playbooks"]
        and archived["code"] not in result["sections"]["playbooks"],
        "propuestos y archivados quedan fuera de sections.playbooks",
    )

    tight = render_brief(
        conn,
        {"brief_budget_chars": 2000, "playbook_index_chars": 20},
        budget=2000,
    )
    h.ok(
        "## Playbooks" not in tight["text"],
        "playbook_index_chars acota el índice y lo omite si no cabe",
    )
    scoped = render_brief(conn, config, budget=2000, section="project")
    h.ok(
        "## Playbooks" not in scoped["text"]
        and notes["code"] in scoped["text"],
        "una sección concreta no arrastra el índice de playbooks",
    )


def _hints(h: Harness) -> None:
    conn = h.conn
    memory = add_memory(
        conn,
        title="Memoria de src",
        content="Riesgo en src/a.",
        kind="pitfall",
        paths=["src/a/"],
        source="user",
    )
    relevant = _playbook(
        conn,
        "revisar-pr",
        title="Revisar PR",
        description="Procedimiento para bug de memoria.",
        content="Cuerpo del playbook.",
        paths=["src/a/"],
    )
    irrelevant = _playbook(
        conn,
        "solo-docs",
        title="Documentar",
        description="Solo documentación general.",
        content="Cuerpo de docs.",
        paths=["docs/"],
    )
    proposed = _playbook(
        conn,
        "propuesto",
        title="Propuesto",
        description="Propuesta sin aprobar.",
        content="Cuerpo propuesto.",
        paths=["src/a/"],
        source="agent",
    )
    ticket = {
        "code": "T-X",
        "kind": "bug",
        "paths": ["src/a/x.py"],
        "tags": ["memoria"],
    }
    hints = memory_handlers.hints(conn, ticket)
    playbooks = [item for item in hints if item.get("kind") == "playbook"]
    slugs = {item["slug"] for item in playbooks}
    h.ok(
        any(item["code"] == memory["code"] for item in hints),
        "los hints siguen incluyendo memorias normales",
    )
    h.eq(
        slugs,
        {"revisar-pr"},
        "solo el playbook relevante por ruta entra en los hints",
    )
    h.ok(
        relevant["code"] == playbooks[0]["code"]
        and playbooks[0]["title"] == "Revisar PR"
        and playbooks[0]["description"] == "Procedimiento para bug de memoria."
        and "ruta" in playbooks[0]["reason"]
        and "content" not in playbooks[0],
        "el hint de playbook lleva slug, título, descripción y motivo",
    )
    h.ok(
        irrelevant["code"] not in {item.get("code") for item in hints}
        and proposed["code"] not in {item.get("code") for item in hints},
        "playbooks irrelevantes o propuestos no se sugieren",
    )

    by_kind = memory_handlers.hints(
        conn, {"code": "T-X", "kind": "bug", "paths": [], "tags": []}
    )
    h.ok(
        any(
            item.get("kind") == "playbook" and item["slug"] == "revisar-pr"
            for item in by_kind
        ),
        "un playbook que menciona el tipo del ticket también se sugiere",
    )

    _playbook(
        conn,
        "otro-bug",
        title="Otro bug",
        description="Segundo procedimiento de bug.",
        content="Cuerpo dos.",
    )
    _playbook(
        conn,
        "tercer-bug",
        title="Tercer bug",
        description="Tercer procedimiento de bug.",
        content="Cuerpo tres.",
    )
    capped = memory_handlers.hints(
        conn, {"code": "T-X", "kind": "bug", "paths": ["src/a/"], "tags": []}
    )
    h.ok(
        len([item for item in capped if item.get("kind") == "playbook"]) == 2,
        "los hints de playbook se limitan a dos",
    )


def _reviewer(h: Harness) -> None:
    text = REVIEWER.read_text(encoding="utf-8")
    h.ok(
        "memory_checklist" in text
        and "memory_playbook" in text
        and "tipo de memoria" in text,
        "el reviewer usa memory_checklist y memory_playbook y aclara kind",
    )
    h.ok(
        "memory_search" in text
        and "pitfall" in text
        and "no lo apruebes" in text,
        "el reviewer busca pitfalls y propone sin aprobar",
    )


def run(h: Harness) -> None:
    _brief(h)
    _hints(h)
    _reviewer(h)
