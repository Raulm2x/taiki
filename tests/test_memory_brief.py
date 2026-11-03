"""Brief operativo: secciones, orden, escape, presupuesto y escritura."""

from __future__ import annotations

from pathlib import Path

from ticketlib import db
from ticketlib.memory import add_memory, patch_memory
from ticketlib.memory.brief import render_brief, write_brief
from ticketlib.memory.config import resolve_memory_config
from ticketlib.memory.render import escape_text
from tests.support import Harness


def _seed(conn) -> dict[str, str]:
    rama = add_memory(
        conn,
        title="Rama de integración",
        content="develop integra con --ff-only.",
        kind="convention",
        key="rama-integracion",
        scope="project",
        source="user",
    )
    commits = add_memory(
        conn,
        title="Commits pequeños",
        content="Prefiero commits pequeños con el código en el asunto.",
        kind="preference",
        key="commits-pequenos",
        scope="user",
        source="user",
    )
    flujo = add_memory(
        conn,
        title="Flujo de tests",
        content="Primero los tests, luego el commit.",
        kind="workflow",
        key="flujo-tests",
        scope="project",
        source="user",
    )
    propuesta = add_memory(
        conn,
        title="Propuesta de decisión",
        content="Revisar el presupuesto del brief.",
        kind="decision",
        key="propuesta-brief",
        scope="project",
        source="agent",
        confidence=0.9,
        evidence=[{"kind": "file", "ref": "README.md"}],
    )
    return {
        "rama": rama["code"],
        "commits": commits["code"],
        "flujo": flujo["code"],
        "propuesta": propuesta["code"],
    }


def run(h: Harness) -> None:
    conn = h.conn
    codes = _seed(conn)
    rama_code = codes["rama"]

    result = render_brief(conn, {}, budget=5000)
    text = result["text"]
    h.ok(
        "% — " in text and "chars]" in text and result["version"] == 1,
        "la cabecera incluye porcentaje y caracteres y el brief declara versión",
    )
    h.ok(
        "## Proyecto" in text
        and "## Usuario" in text
        and "## Routing observado" in text
        and "## Pendientes" in text
        and codes["rama"] in result["sections"]["project"]
        and codes["commits"] in result["sections"]["user"]
        and codes["flujo"] in result["sections"]["routing"]
        and codes["propuesta"] in result["sections"]["pending"],
        "el brief incluye las cuatro secciones y cada memoria cae en la suya",
    )
    h.eq(
        render_brief(conn, {}, budget=5000)["text"],
        text,
        "dos renders seguidos son idénticos",
    )
    active_content = sum(len(item) for item in (
        "develop integra con --ff-only.",
        "Prefiero commits pequeños con el código en el asunto.",
        "Primero los tests, luego el commit.",
    ))
    pending_line = next(
        line for line in text.splitlines() if "taiki memory pending" in line
    )
    h.ok(
        result["usage"]["used"] == active_content + len(pending_line)
        and "Revisar el presupuesto del brief." not in text
        and "Propuesta de decisión" not in text,
        "el uso cuenta contenido y pendientes y no vuelca las propuestas",
    )

    first = add_memory(
        conn,
        title="Entrada normal",
        content="Contenido normal.",
        kind="fact",
        key="entrada-normal",
        scope="project",
        source="user",
    )
    pinned = add_memory(
        conn,
        title="Entrada fijada",
        content="Contenido fijado.",
        kind="fact",
        key="entrada-fijada",
        scope="project",
        source="user",
        pinned=True,
    )
    ordered = render_brief(conn, {}, budget=5000)["text"]
    h.ok(
        ordered.index(pinned["code"]) < ordered.index(first["code"]),
        "las memorias pinned aparecen primero",
    )

    escaped = add_memory(
        conn,
        title="Entrada multilínea",
        content="primera\nsegunda linea",
        kind="fact",
        key="entrada-multilinea",
        scope="project",
        source="user",
    )
    escaped_text = render_brief(conn, {}, budget=5000)["text"]
    h.ok(
        escaped["content"] not in escaped_text
        and "primera\\nsegunda linea" in escaped_text
        and escape_text("a\nb\tc\x07") == "a\\nb\\tc\\x07",
        "los saltos se escapan en el brief y en escape_text",
    )

    filtered = render_brief(conn, {}, budget=5000, section="user")
    h.ok(
        "## Usuario" in filtered["text"]
        and "## Proyecto" not in filtered["text"],
        "--section user limita el brief al usuario",
    )

    budget_conn = db.connect(h.tmp / "brief-budget.db")
    try:
        one = add_memory(
            budget_conn,
            title="Bloque uno",
            content="A" * 20,
            kind="fact",
            key="bloque-uno",
            source="user",
        )
        two = add_memory(
            budget_conn,
            title="Bloque dos",
            content="B" * 20,
            kind="fact",
            key="bloque-dos",
            source="user",
        )
        tight = render_brief(budget_conn, {}, budget=25)
        h.ok(
            tight["usage"]["used"] == 20
            and len(tight["omitted"]) == 1
            and tight["omitted"][0] in (one["code"], two["code"])
            and "entradas omitidas; usa memory_search" in tight["text"],
            "el brief no supera el presupuesto y nombra la entrada omitida",
        )
        included = one["code"] if one["code"] not in tight["omitted"] else two["code"]
        content = "A" * 20 if included == one["code"] else "B" * 20
        h.ok(
            included in tight["text"] and content in tight["text"],
            "la entrada incluida se conserva entera, sin truncar",
        )
    finally:
        budget_conn.close()

    empty_conn = db.connect(h.tmp / "brief-empty.db")
    try:
        empty = render_brief(empty_conn, {}, budget=100)
        h.ok(
            "## Routing" not in empty["text"]
            and "## Pendientes" not in empty["text"]
            and "(sin memorias)" in empty["text"]
            and empty["usage"]["pending"] == 0,
            "sin datos se omiten routing y pendientes y usage.pending es cero",
        )
    finally:
        empty_conn.close()

    pending_conn = db.connect(h.tmp / "brief-pending.db")
    try:
        proposals = [
            add_memory(
                pending_conn,
                title=f"Propuesta {index}",
                content=f"Contenido propuesto {index}.",
                kind="decision",
                key=f"propuesta-{index}",
                source="agent",
                evidence=[{"kind": "ticket", "ref": "T-0053"}],
            )
            for index in range(1, 8)
        ]
        many = render_brief(pending_conn, {}, budget=5000)
        codes = [item["code"] for item in proposals]
        pending_line = next(
            line
            for line in many["text"].splitlines()
            if "taiki memory pending" in line
        )
        h.ok(
            many["usage"]["pending"] == 7
            and many["sections"]["pending"] == codes
            and all(code in pending_line for code in codes[:5])
            and codes[5] not in pending_line
            and "(+2 más)" in many["text"],
            "el brief compacta varias propuestas a cinco códigos con (+2 más)",
        )
        h.ok(
            all(item["content"] not in many["text"] for item in proposals),
            "las propuestas no vuelcan su contenido",
        )
        short = (
            "- 7 propuestas pendientes de aprobación · "
            "revisa con taiki memory pending"
        )
        tight = render_brief(pending_conn, {}, budget=len(short) + 10)
        h.ok(
            "## Pendientes" in tight["text"]
            and short in tight["text"]
            and all(item["code"] not in tight["text"] for item in proposals)
            and tight["usage"]["pending"] == 7,
            "si no cabe la línea completa queda el recuento",
        )
    finally:
        pending_conn.close()

    data = write_brief(h.tmp, conn, {}, budget=5000)
    target = Path(h.tmp) / ".taiki" / "generated" / "brief.md"
    folder = target.parent
    h.ok(
        data["path"] == str(target)
        and target.read_text(encoding="utf-8").startswith(
            "generated by taiki; do not edit"
        )
        and [item.name for item in folder.iterdir()] == ["brief.md"],
        "write_brief devuelve la ruta, escribe la cabecera y no deja temporales",
    )
    add_memory(
        conn,
        title="Entrada posterior",
        content="Aparece tras reescribir.",
        kind="fact",
        key="entrada-posterior",
        scope="project",
        source="user",
    )
    write_brief(h.tmp, conn, {}, budget=5000)
    h.ok(
        "Aparece tras reescribir." in target.read_text(encoding="utf-8"),
        "reescribir reemplaza el artefacto completo",
    )

    root_config = {"project": "taiki-sys", "guard": True, "scope": {}, "verify": {}}
    h.ok(
        render_brief(conn, root_config, budget=5000)["text"]
        == render_brief(conn, {}, budget=5000)["text"]
        and resolve_memory_config({"project": "x", "memory": {"stale_after_days": 10}})[
            "stale_after_days"
        ]
        == 10,
        "el brief acepta el config raíz sin memory y el bloque memory explícito se valida",
    )

    patch_memory(
        conn,
        rama_code,
        ticket_kinds=["bug"],
        paths=["src/a.py", "src/b.py"],
    )
    long_title = "T" * 130
    add_memory(
        conn,
        title=long_title,
        content="Título largo.",
        kind="fact",
        key="titulo-largo",
        scope="project",
        source="user",
    )
    index_text = render_brief(conn, {"brief_mode": "index"}, budget=5000)["text"]
    grouped_text = render_brief(conn, {"brief_mode": "grouped"}, budget=5000)["text"]
    full_text = render_brief(conn, {"brief_mode": "full"}, budget=5000)["text"]
    h.ok(
        rama_code in index_text
        and "Rama de integración" in index_text
        and "memory_search" in index_text
        and "key=rama-integracion" in index_text
        and "tickets=bug" in index_text
        and "paths=src/a.py" in index_text
        and long_title[:80] in index_text
        and long_title not in index_text
        and "develop integra con --ff-only." not in index_text
        and "## Rama" in grouped_text
        and rama_code in grouped_text
        and "memory_search rama" in grouped_text
        and "## Rama" not in index_text
        and "develop integra con --ff-only." in full_text,
        "brief_mode=index lista código, título y disparadores sin contenido destilado; "
        "grouped agrupa por dominio sin contenido; full lo conserva",
    )
