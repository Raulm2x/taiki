"""Contrato del CLI de playbooks: add, list, show, export y archive."""

from __future__ import annotations

import contextlib
import io
import json
from pathlib import Path

import cli
from tests.support import Harness


def _run(argv: list[str]) -> tuple[int, str, str]:
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        try:
            code = cli.main(argv)
        except SystemExit as exc:  # argparse (por ejemplo, falta --content)
            code = int(exc.code or 0)
    return code, out.getvalue(), err.getvalue()


def _json(argv: list[str]) -> tuple[int, dict]:
    code, out, err = _run(argv)
    if code != 0:
        raise AssertionError(f"CLI falló ({code}): {err}")
    return code, json.loads(out)


def _support_files(h: Harness) -> tuple[Path, Path, Path]:
    folder = h.tmp / "supports"
    folder.mkdir()
    reference = folder / "checklist.md"
    reference.write_text("- tests\n", encoding="utf-8")
    script = folder / "run.sh"
    script.write_text("#!/bin/sh\necho ok\n", encoding="utf-8")
    template = folder / "brief.tmpl"
    template.write_text("Hola {{ nombre }}\n", encoding="utf-8")
    return reference, script, template


def _add(reference: Path, script: Path, template: Path, *, slug: str = "revisar-pr"):
    return _json(
        [
            "memory",
            "playbook",
            "add",
            "--slug",
            slug,
            "--title",
            "Revisar PR",
            "--description",
            "Checklist para revisar un pull request.",
            "--content",
            "1. Lee el diff.\n2. Ejecuta los gates.",
            "--support",
            str(reference),
            "--support",
            str(script),
            "--support",
            str(template),
            "--evidence",
            "T-0047",
            "--json",
        ]
    )


def run(h: Harness) -> None:
    reference, script, template = _support_files(h)
    _, created = _add(reference, script, template)
    h.eq(created["kind"], "playbook", "playbook add crea kind=playbook")
    h.eq(created["key"], "playbook:revisar-pr", "playbook add fija la clave")
    h.eq(created["status"], "active", "el playbook del usuario nace activo")
    paths = [item["path"] for item in created["files"]]
    h.eq(
        paths,
        ["references/checklist.md", "scripts/run.sh", "templates/brief.tmpl"],
        "los apoyos se clasifican por extensión",
    )
    h.eq(
        created["evidence"][0]["ref"],
        "T-0047",
        "playbook add guarda la evidencia",
    )

    body = h.tmp / "cuerpo.md"
    body.write_text("Cuerpo leído del disco.\n", encoding="utf-8")
    _, from_file = _json(
        [
            "memory",
            "playbook",
            "add",
            "--slug",
            "desde-fichero",
            "--title",
            "Desde fichero",
            "--description",
            "Cuerpo leído de un fichero.",
            "--file",
            str(body),
            "--evidence",
            "T-0047",
            "--json",
        ]
    )
    h.eq(
        from_file["content"],
        "Cuerpo leído del disco.",
        "playbook add --file lee el cuerpo del disco",
    )

    invalid_slug, _, err = _run(
        [
            "memory",
            "playbook",
            "add",
            "--slug",
            "Slug Invalido",
            "--title",
            "X",
            "--description",
            "Y",
            "--content",
            "Z",
            "--evidence",
            "T-0047",
        ]
    )
    h.ok(
        invalid_slug == 1 and "Slug de playbook inválido" in err,
        "playbook add rechaza un slug inválido",
    )
    missing, _, err = _run(
        [
            "memory",
            "playbook",
            "add",
            "--slug",
            "sin-apoyo",
            "--title",
            "Sin apoyo",
            "--description",
            "Y",
            "--content",
            "Z",
            "--support",
            str(h.tmp / "no-existe.md"),
        ]
    )
    h.ok(
        missing == 1 and "no encontrado" in err,
        "playbook add rechaza un apoyo inexistente",
    )
    without_source, _, _ = _run(
        [
            "memory",
            "playbook",
            "add",
            "--slug",
            "sin-cuerpo",
            "--title",
            "Sin cuerpo",
            "--description",
            "Y",
        ]
    )
    h.eq(without_source, 2, "playbook add exige --content o --file")

    _, listed = _json(["memory", "playbook", "list", "--json"])
    h.eq(listed["count"], 2, "playbook list devuelve los playbooks creados")
    h.eq(
        listed["playbooks"][0]["kind"],
        "playbook",
        "playbook list solo incluye playbooks",
    )
    plain_code, plain_out, _ = _run(["memory", "playbook", "list"])
    h.ok(
        plain_code == 0 and "revisar-pr" in plain_out,
        "playbook list imprime slug y estado",
    )

    _, shown = _json(["memory", "playbook", "show", "revisar-pr", "--json"])
    h.eq(shown["code"], created["code"], "playbook show acepta el slug")
    h.eq(
        shown["files"][0]["content"],
        "- tests\n",
        "playbook show incluye el contenido de los apoyos",
    )
    h.ok(shown["evidence"], "playbook show incluye la evidencia")
    _, by_code = _json(["memory", "playbook", "show", created["code"], "--json"])
    h.eq(by_code["id"], created["id"], "playbook show acepta el código")
    missing_show, _, err = _run(["memory", "playbook", "show", "no-existe"])
    h.ok(
        missing_show == 1 and "no encontrado" in err,
        "playbook show falla con un slug desconocido",
    )
    _, detail_out, _ = _run(["memory", "playbook", "show", "revisar-pr"])
    h.ok(
        "Evidencia: ticket:T-0047" in detail_out
        and "references/checklist.md" in detail_out,
        "playbook show imprime evidencia y ficheros",
    )

    from commands import memory as memory_command

    original = memory_command.workspace.project_root
    memory_command.workspace.project_root = lambda: h.tmp
    try:
        _, exported = _json(
            ["memory", "playbook", "export", "revisar-pr", "--json"]
        )
        skill = Path(exported["path"])
        h.ok(skill.is_file(), "playbook export escribe SKILL.md")
        h.ok(
            'name: "revisar-pr"' in skill.read_text(encoding="utf-8"),
            "el SKILL.md exportado lleva frontmatter",
        )
        h.eq(
            len(exported["files"]),
            3,
            "playbook export escribe también los apoyos",
        )
        _, exported_all = _json(
            ["memory", "playbook", "export", "--all", "--json"]
        )
        h.eq(
            exported_all["exported"],
            ["desde-fichero", "revisar-pr"],
            "playbook export --all exporta los activos",
        )
        plain_export, plain_export_out, _ = _run(
            ["memory", "playbook", "export", "--all"]
        )
        h.ok(
            plain_export == 0 and "Exportados 2 playbook(s)" in plain_export_out,
            "playbook export imprime el resumen",
        )
        no_ref, _, err = _run(["memory", "playbook", "export"])
        h.ok(
            no_ref == 1 and "Indica un slug" in err,
            "playbook export exige slug o --all",
        )
        both, _, err = _run(
            ["memory", "playbook", "export", "revisar-pr", "--all"]
        )
        h.ok(
            both == 1 and "no ambos" in err,
            "playbook export rechaza slug y --all a la vez",
        )
    finally:
        memory_command.workspace.project_root = original

    no_why, _, _ = _run(["memory", "playbook", "archive", "revisar-pr"])
    h.eq(no_why, 2, "playbook archive exige --why")
    _, archived = _json(
        [
            "memory",
            "playbook",
            "archive",
            "revisar-pr",
            "--why",
            "obsoleto",
            "--json",
        ]
    )
    h.eq(archived["status"], "archived", "playbook archive archiva sin borrar")
    h.ok(
        _json(["memory", "playbook", "show", "revisar-pr", "--json"])[1][
            "status"
        ]
        == "archived",
        "el playbook archivado sigue existiendo",
    )
    _, active = _json(["memory", "playbook", "list", "--json"])
    h.eq(active["count"], 1, "playbook list omite los archivados por defecto")
    _, everything = _json(["memory", "playbook", "list", "--all", "--json"])
    h.eq(everything["count"], 2, "playbook list --all incluye los archivados")
    archive_out_code, archive_out, _ = _run(
        ["memory", "playbook", "archive", "desde-fichero", "--why", "obsoleto"]
    )
    h.ok(
        archive_out_code == 0 and "archivado (no borrado)" in archive_out,
        "playbook archive imprime el aviso de no borrado",
    )
