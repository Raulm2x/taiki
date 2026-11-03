"""Playbooks: slug, validación, export a SKILL.md e índice del brief."""

from __future__ import annotations

from pathlib import Path

from ticketlib import db
from ticketlib.domain import ValidationError
from ticketlib.memory import (
    add_memory,
    archive_playbook,
    brief_index,
    create_playbook,
    export_all,
    export_playbook,
    get_playbook,
    list_playbooks,
    update_playbook,
)
from ticketlib.memory.models import parse_playbook_key, playbook_key
from ticketlib.memory.playbooks import validate_playbook_slug
from tests.support import Harness


def _rejects(h: Harness, cases: list, message: str) -> None:
    """Agrupa casos de rechazo en una sola comprobación de presupuesto."""
    rejected = 0
    for case in cases:
        try:
            case()
        except ValidationError:
            rejected += 1
    h.eq(rejected, len(cases), message)


def _new(
    conn,
    slug: str,
    *,
    title: str | None = None,
    description: str = "Procedimiento para revisar cambios sin romper develop.",
    content: str = "1. Lee el diff.\n2. Ejecuta los gates.",
    files: list | None = None,
    source: str = "user",
    evidence: list | None = None,
    config: dict | None = None,
):
    return create_playbook(
        conn,
        slug=slug,
        title=title if title is not None else slug.replace("-", " ").title(),
        description=description,
        content=content,
        evidence=evidence or [{"kind": "ticket", "ref": "T-0047"}],
        files=files,
        source=source,
        config=config,
    )


def _slug(h: Harness) -> None:
    accepted = ("a", "revisar-pr", "p3-playbook", "x1-y2-3")
    h.eq(
        [validate_playbook_slug(item) for item in accepted],
        list(accepted),
        "se aceptan slugs en minúsculas con guiones simples",
    )
    _rejects(
        h,
        [
            lambda: validate_playbook_slug(""),
            lambda: validate_playbook_slug("Revisar"),
            lambda: validate_playbook_slug("con espacios"),
            lambda: validate_playbook_slug("-empieza"),
            lambda: validate_playbook_slug("termina-"),
            lambda: validate_playbook_slug("doble--guion"),
            lambda: validate_playbook_slug("ñ"),
            lambda: validate_playbook_slug("a" * 65),
        ],
        "se rechazan slugs vacíos, mayúsculas, símbolos y excesivos",
    )
    h.eq(
        playbook_key("revisar-pr"),
        "playbook:revisar-pr",
        "la clave de playbook usa el prefijo estable",
    )
    h.eq(
        parse_playbook_key("playbook:revisar-pr"),
        "revisar-pr",
        "parse_playbook_key recupera el slug",
    )
    h.ok(
        parse_playbook_key("playbook:Revisar") is None,
        "parse_playbook_key ignora claves inválidas",
    )


def _validation(h: Harness) -> None:
    conn = h.conn
    h.raises(
        ValidationError,
        lambda: create_playbook(
            conn,
            slug="sin-evidencia",
            title="Sin evidencia",
            description="No debería guardarse.",
            content="Cuerpo.",
            evidence=[],
            source="user",
        ),
        "create_playbook exige evidencia",
    )
    h.raises(
        ValidationError,
        lambda: _new(conn, "descripcion-larga", description="x" * 241),
        "la descripción no puede superar 240 caracteres",
    )
    h.raises(
        ValidationError,
        lambda: _new(
            conn,
            "cuerpo-largo",
            content="x" * 51,
            config={"max_entry_chars": 10, "max_playbook_chars": 50},
        ),
        "el contenido respeta max_playbook_chars",
    )
    h.raises(
        ValidationError,
        lambda: _new(conn, "titulo-vacio", title=""),
        "el nombre del playbook es obligatorio",
    )
    h.raises(
        ValidationError,
        lambda: _new(conn, "Clave-Rara"),
        "un slug inválido no crea memoria",
    )

    created = _new(
        conn,
        "revisar-pr",
        description="Checklist de revisión de pull requests.",
        files=[{"path": "references/checklist.md", "content": "- tests\n"}],
    )
    h.eq(created["kind"], "playbook", "el playbook se guarda con kind=playbook")
    h.eq(created["key"], "playbook:revisar-pr", "la clave incluye el slug")
    h.eq(created["status"], "active", "source=user nace activo")
    h.eq(created["description"], "Checklist de revisión de pull requests.", "guarda description")
    h.eq(len(created["files"]), 1, "guarda los ficheros de apoyo")
    h.ok(len(created["files"][0]["sha256"]) == 64, "calcula el sha256 del apoyo")

    by_slug = get_playbook(conn, "revisar-pr")
    by_code = get_playbook(conn, created["code"])
    h.eq(by_slug["code"], created["code"], "get_playbook resuelve por slug")
    h.eq(by_code["id"], created["id"], "get_playbook resuelve por código")
    h.eq(
        by_slug["files"][0]["content"],
        "- tests\n",
        "get_playbook incluye el contenido de los apoyos para exportar",
    )
    h.raises(
        ValidationError,
        lambda: get_playbook(conn, "no-existe"),
        "un slug desconocido falla",
    )
    normal = add_memory(
        conn,
        title="Memoria normal",
        content="Contenido destilado.",
        source="user",
    )
    h.raises(
        ValidationError,
        lambda: get_playbook(conn, normal["code"]),
        "get_playbook rechaza una memoria que no es playbook",
    )
    h.eq(len(list_playbooks(conn)), 1, "list_playbooks solo lista playbooks")
    h.eq(
        len(list_playbooks(conn, status="active")),
        1,
        "list_playbooks filtra por estado",
    )


def _files(h: Harness) -> None:
    conn = h.conn
    before = len(list_playbooks(conn))
    _rejects(
        h,
        [
            lambda path=path: _new(
                conn, "traversal", files=[{"path": path, "content": "x"}]
            )
            for path in (
                "../fuera.md",
                "/etc/passwd",
                "docs/readme.md",
                "references\\win.md",
                "references/../../fuera.md",
                "",
            )
        ],
        "se rechazan absolutos, traversal y prefijos no permitidos",
    )
    h.eq(
        len(list_playbooks(conn)),
        before,
        "los ficheros rechazados no crean playbooks",
    )

    created = _new(
        conn,
        "con-apoyos",
        files=[
            {"path": "scripts/run.sh", "content": "#!/bin/sh\necho ok\n"},
            {"path": "assets/logo.png", "content": "png"},
            {"path": "templates/brief.md", "content": "plantilla"},
            {"path": "references/sub/nota.md", "content": "nota"},
        ],
    )
    h.eq(len(created["files"]), 4, "se guardan los cuatro tipos de apoyo")
    h.ok(
        any(item["path"] == "references/sub/nota.md" for item in created["files"]),
        "se admiten rutas anidadas dentro de references",
    )

    updated = update_playbook(
        conn,
        "con-apoyos",
        files=[{"path": "references/sub/nota.md", "content": "nota nueva"}],
    )
    h.eq(len(updated["files"]), 1, "patch reemplaza los ficheros de apoyo")
    h.eq(
        get_playbook(conn, "con-apoyos")["files"][0]["content"],
        "nota nueva",
        "el apoyo actualizado conserva su contenido",
    )


def _export(h: Harness) -> None:
    conn = h.conn
    root = h.tmp / "workspace"
    _new(
        conn,
        "export-demo",
        title="Export demo",
        description="Demo de export a skill.",
        content="Paso uno.\nPaso dos.",
        evidence=[
            {"kind": "ticket", "ref": "T-0047"},
            {"kind": "commit", "ref": "abc1234"},
        ],
        files=[
            {"path": "references/check.md", "content": "- tests\n"},
            {"path": "scripts/run.sh", "content": "#!/bin/sh\necho ok\n"},
        ],
    )
    memory = get_playbook(conn, "export-demo")
    result = export_playbook(root, memory)
    skill = Path(result["path"])
    h.ok(skill.is_file(), "export_playbook escribe SKILL.md")
    h.eq(skill.parent.name, "export-demo", "el skill vive en su carpeta de slug")
    text = skill.read_text(encoding="utf-8")
    h.ok(text.startswith("---\n"), "el skill empieza con frontmatter")
    h.ok('name: "export-demo"' in text, "el frontmatter lleva el name del slug")
    h.ok('description: "Demo de export a skill."' in text, "lleva description")
    h.ok("  taiki:" in text, "el frontmatter lleva metadata taiki")
    h.ok(f'code: "{memory["code"]}"' in text, "metadata incluye el código")
    h.ok('"ticket:T-0047"' in text, "metadata incluye la evidencia de ticket")
    h.ok('"commit:abc1234"' in text, "metadata incluye la evidencia de commit")
    h.ok("Paso uno." in text, "el cuerpo del SKILL.md es el contenido")
    h.ok(
        (skill.parent / "references/check.md").read_text(encoding="utf-8")
        == "- tests\n",
        "los apoyos se escriben con su contenido",
    )
    h.ok(
        (skill.parent / "scripts/run.sh").is_file(),
        "los scripts de apoyo se escriben",
    )

    again = export_playbook(root, get_playbook(conn, "export-demo"))
    h.eq(
        skill.read_text(encoding="utf-8"),
        text,
        "exportar dos veces produce el mismo SKILL.md",
    )
    h.eq(again["removed"], [], "la reexportación no retira apoyos vigentes")

    update_playbook(
        conn,
        "export-demo",
        files=[{"path": "references/check.md", "content": "- tests\n"}],
    )
    third = export_playbook(root, get_playbook(conn, "export-demo"))
    h.ok(
        not (skill.parent / "scripts/run.sh").exists(),
        "la reexportación retira apoyos que ya no están en la memoria",
    )
    h.eq(third["removed"], ["scripts/run.sh"], "el recibo lista el apoyo retirado")


def _export_all(h: Harness) -> None:
    conn = db.connect(h.tmp / "skills.db")
    root = h.tmp / "workspace-all"
    try:
        _new(conn, "skill-activo", title="Skill activo")
        archived = _new(conn, "skill-archivado", title="Skill archivado")
        _new(
            conn,
            "skill-propuesto",
            title="Skill propuesto",
            source="agent",
            evidence=[{"kind": "file", "ref": "ticketlib/memory/playbooks.py"}],
        )
        manual = root / ".taiki/generated/skills/manual"
        manual.mkdir(parents=True)
        (manual / "SKILL.md").write_text("manual\n", encoding="utf-8")

        archive_playbook(conn, archived["code"], reason="Ya no aplica.")
        export_playbook(root, get_playbook(conn, "skill-archivado"))
        result = export_all(conn, root)
        h.eq(
            result["exported"],
            ["skill-activo"],
            "solo se exportan playbooks activos",
        )
        h.ok(
            (root / ".taiki/generated/skills/skill-activo/SKILL.md").is_file(),
            "el playbook activo se exporta",
        )
        h.ok(
            not (root / ".taiki/generated/skills/skill-propuesto").exists(),
            "un playbook propuesto no se exporta",
        )
        h.ok(
            (manual / "SKILL.md").is_file(),
            "export_all no toca directorios que no son playbooks",
        )
        h.eq(
            result["removed"],
            ["skill-archivado"],
            "el skill de un playbook archivado se retira",
        )
        h.ok(
            not (root / ".taiki/generated/skills/skill-archivado").exists(),
            "la carpeta obsoleta desaparece",
        )
        h.ok(
            (root / ".taiki/generated/skills/skill-activo/SKILL.md").is_file(),
            "los skills vigentes se conservan",
        )
    finally:
        conn.close()


def _brief(h: Harness) -> None:
    conn = db.connect(h.tmp / "brief.db")
    try:
        for slug, description in (
            ("uno", "Primer procedimiento compacto."),
            ("dos", "Segundo procedimiento compacto."),
            ("tres", "Tercer procedimiento compacto."),
        ):
            _new(conn, slug, description=description)
        short = brief_index(conn, {"playbook_index_chars": 90})
        h.ok(len(short["text"]) <= 90, "el índice respeta playbook_index_chars")
        h.ok(short["entries"], "el índice incluye al menos un playbook")
        first = short["entries"][0]
        h.eq(
            sorted(first),
            ["code", "description", "slug", "title"],
            "cada entrada lleva slug, title, description y code",
        )
        h.eq(
            short["omitted"],
            3 - len(short["entries"]),
            "el índice cuenta las entradas omitidas",
        )

        whole = brief_index(conn, {"playbook_index_chars": 5000})
        h.eq(len(whole["entries"]), 3, "con espacio caben todos los activos")
        h.ok("uno" in whole["text"] and "M-" in whole["text"], "el texto es compacto")
        h.ok(
            "1. Lee el diff." not in whole["text"],
            "el índice nunca incluye el cuerpo del playbook",
        )

        archive_playbook(conn, "dos", reason="Obsoleto.")
        active = brief_index(conn, {"playbook_index_chars": 5000})
        h.ok(
            all(item["slug"] != "dos" for item in active["entries"]),
            "un playbook archivado sale del índice",
        )
        _new(
            conn,
            "propuesto",
            source="agent",
            evidence=[{"kind": "file", "ref": "ticketlib/memory/budget.py"}],
        )
        active = brief_index(conn, {"playbook_index_chars": 5000})
        h.ok(
            all(item["slug"] != "propuesto" for item in active["entries"]),
            "un playbook propuesto no entra en el índice",
        )
    finally:
        conn.close()


def _budget(h: Harness) -> None:
    conn = db.connect(h.tmp / "budget.db")
    try:
        cfg = {"project_budget_chars": 100, "max_playbook_chars": 12000}
        created = _new(
            conn,
            "largo",
            content="x" * 4000,
            config=cfg,
        )
        h.eq(
            created["budget"]["used"],
            0,
            "el cuerpo del playbook no consume el presupuesto del scope",
        )
        normal = add_memory(
            conn,
            title="Nota normal",
            content="n" * 60,
            source="user",
            config=cfg,
        )
        h.eq(
            normal["budget"]["used"],
            60,
            "las memorias normales siguen contando en el presupuesto",
        )
    finally:
        conn.close()


def _update(h: Harness) -> None:
    conn = h.conn
    _new(conn, "editable", title="Antes", content="Cuerpo anterior.")
    changed = update_playbook(
        conn,
        "editable",
        title="Después",
        description="Descripción nueva.",
        content="Cuerpo nuevo.",
    )
    h.eq(changed["title"], "Después", "update_playbook cambia el nombre")
    h.eq(changed["content"], "Cuerpo nuevo.", "update_playbook cambia el cuerpo")
    h.raises(
        ValidationError,
        lambda: update_playbook(
            conn, "editable", description="x" * 241
        ),
        "update_playbook valida la descripción",
    )
    h.eq(
        get_playbook(conn, "editable")["description"],
        "Descripción nueva.",
        "el rechazo no cambia el playbook",
    )


def run(h: Harness) -> None:
    _slug(h)
    _validation(h)
    _files(h)
    _export(h)
    _export_all(h)
    _brief(h)
    _budget(h)
    _update(h)
