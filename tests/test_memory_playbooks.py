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
    h.ok(
        playbook_key("revisar-pr") == "playbook:revisar-pr"
        and parse_playbook_key("playbook:revisar-pr") == "revisar-pr",
        "la clave de playbook usa el prefijo estable y se parsea",
    )
    h.ok(
        parse_playbook_key("playbook:Revisar") is None,
        "parse_playbook_key ignora claves inválidas",
    )


def _validation(h: Harness) -> None:
    conn = h.conn
    _rejects(
        h,
        [
            lambda: create_playbook(
                conn,
                slug="sin-evidencia",
                title="Sin evidencia",
                description="No debería guardarse.",
                content="Cuerpo.",
                evidence=[],
                source="user",
            ),
            lambda: _new(conn, "descripcion-larga", description="x" * 241),
            lambda: _new(
                conn,
                "cuerpo-largo",
                content="x" * 51,
                config={"max_entry_chars": 10, "max_playbook_chars": 50},
            ),
            lambda: _new(conn, "titulo-vacio", title=""),
            lambda: _new(conn, "Clave-Rara"),
        ],
        "create_playbook exige evidencia, límites de tamaño, título y slug válidos",
    )

    created = _new(
        conn,
        "revisar-pr",
        description="Checklist de revisión de pull requests.",
        files=[{"path": "references/checklist.md", "content": "- tests\n"}],
    )
    h.ok(
        created["kind"] == "playbook"
        and created["key"] == "playbook:revisar-pr"
        and created["status"] == "active"
        and created["description"] == "Checklist de revisión de pull requests."
        and len(created["files"]) == 1
        and len(created["files"][0]["sha256"]) == 64,
        "el playbook se guarda con kind, clave, estado, description, apoyos y sha256",
    )

    by_slug = get_playbook(conn, "revisar-pr")
    by_code = get_playbook(conn, created["code"])
    h.ok(
        by_slug["code"] == created["code"]
        and by_code["id"] == created["id"]
        and by_slug["files"][0]["content"] == "- tests\n",
        "get_playbook resuelve por slug y código e incluye el contenido de apoyos",
    )
    normal = add_memory(
        conn,
        title="Memoria normal",
        content="Contenido destilado.",
        source="user",
    )
    _rejects(
        h,
        [
            lambda: get_playbook(conn, "no-existe"),
            lambda: get_playbook(conn, normal["code"]),
        ],
        "get_playbook falla con slug desconocido o memoria que no es playbook",
    )
    h.ok(
        len(list_playbooks(conn)) == 1
        and len(list_playbooks(conn, status="active")) == 1,
        "list_playbooks solo lista playbooks y filtra por estado",
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
    rejected_count = len(list_playbooks(conn))
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
    h.ok(
        rejected_count == before
        and len(created["files"]) == 4
        and any(item["path"] == "references/sub/nota.md" for item in created["files"]),
        "los rechazos no crean playbooks y se admiten los cuatro apoyos anidados",
    )

    updated = update_playbook(
        conn,
        "con-apoyos",
        files=[{"path": "references/sub/nota.md", "content": "nota nueva"}],
    )
    h.ok(
        len(updated["files"]) == 1
        and get_playbook(conn, "con-apoyos")["files"][0]["content"] == "nota nueva",
        "patch reemplaza los apoyos y conserva su contenido",
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
    h.ok(
        skill.is_file() and skill.parent.name == "export-demo",
        "export_playbook escribe SKILL.md en la carpeta de su slug",
    )
    text = skill.read_text(encoding="utf-8")
    h.ok(
        text.startswith("---\n")
        and 'name: "export-demo"' in text
        and 'description: "Demo de export a skill."' in text
        and "  taiki:" in text
        and f'code: "{memory["code"]}"' in text,
        "el frontmatter lleva name, description, metadata taiki y código",
    )
    h.ok(
        '"ticket:T-0047"' in text and '"commit:abc1234"' in text,
        "la metadata incluye la evidencia de ticket y commit",
    )
    h.ok(
        "Paso uno." in text
        and (skill.parent / "references/check.md").read_text(encoding="utf-8")
        == "- tests\n"
        and (skill.parent / "scripts/run.sh").is_file(),
        "el cuerpo y los apoyos se escriben con su contenido",
    )

    again = export_playbook(root, get_playbook(conn, "export-demo"))
    h.ok(
        skill.read_text(encoding="utf-8") == text and again["removed"] == [],
        "exportar dos veces produce el mismo SKILL.md sin retirar apoyos",
    )

    update_playbook(
        conn,
        "export-demo",
        files=[{"path": "references/check.md", "content": "- tests\n"}],
    )
    third = export_playbook(root, get_playbook(conn, "export-demo"))
    h.ok(
        not (skill.parent / "scripts/run.sh").exists()
        and third["removed"] == ["scripts/run.sh"],
        "la reexportación retira apoyos ausentes y lo lista en el recibo",
    )


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
        h.ok(
            result["exported"] == ["skill-activo"]
            and (root / ".taiki/generated/skills/skill-activo/SKILL.md").is_file(),
            "solo se exportan playbooks activos y su SKILL.md se escribe",
        )
        h.ok(
            not (root / ".taiki/generated/skills/skill-propuesto").exists()
            and (manual / "SKILL.md").is_file(),
            "un propuesto no se exporta y no se tocan directorios ajenos",
        )
        h.ok(
            result["removed"] == ["skill-archivado"]
            and not (root / ".taiki/generated/skills/skill-archivado").exists()
            and (root / ".taiki/generated/skills/skill-activo/SKILL.md").is_file(),
            "el skill archivado se retira y los vigentes se conservan",
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
        first = short["entries"][0]
        h.ok(
            len(short["text"]) <= 90
            and bool(short["entries"])
            and sorted(first) == ["code", "description", "slug", "title"]
            and short["omitted"] == 3 - len(short["entries"]),
            "el índice respeta playbook_index_chars, lleva claves y cuenta omitidos",
        )

        whole = brief_index(conn, {"playbook_index_chars": 5000})
        h.ok(
            len(whole["entries"]) == 3
            and "uno" in whole["text"]
            and "M-" in whole["text"]
            and "1. Lee el diff." not in whole["text"],
            "con espacio caben todos los activos y el texto no incluye el cuerpo",
        )

        archive_playbook(conn, "dos", reason="Obsoleto.")
        _new(
            conn,
            "propuesto",
            source="agent",
            evidence=[{"kind": "file", "ref": "ticketlib/memory/budget.py"}],
        )
        active = brief_index(conn, {"playbook_index_chars": 5000})
        h.ok(
            all(
                item["slug"] not in ("dos", "propuesto")
                for item in active["entries"]
            ),
            "un playbook archivado o propuesto no entra en el índice",
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
        normal = add_memory(
            conn,
            title="Nota normal",
            content="n" * 60,
            source="user",
            config=cfg,
        )
        h.ok(
            created["budget"]["used"] == 0 and normal["budget"]["used"] == 60,
            "el cuerpo del playbook no consume presupuesto pero las notas sí",
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
    h.ok(
        changed["title"] == "Después" and changed["content"] == "Cuerpo nuevo.",
        "update_playbook cambia el nombre y el cuerpo",
    )
    h.raises(
        ValidationError,
        lambda: update_playbook(conn, "editable", description="x" * 241),
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
