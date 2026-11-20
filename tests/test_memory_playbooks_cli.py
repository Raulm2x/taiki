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
    h.ok(
        created["kind"] == "playbook"
        and created["key"] == "playbook:revisar-pr"
        and created["status"] == "active"
        and [item["path"] for item in created["files"]]
        == ["references/checklist.md", "scripts/run.sh", "templates/brief.tmpl"]
        and created["evidence"][0]["ref"] == "T-0047",
        "playbook add fija kind, clave, estado, apoyos y evidencia",
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

    invalid_slug, _, invalid_err = _run(
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
    missing, _, missing_err = _run(
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
    h.ok(
        invalid_slug == 1
        and "Slug de playbook inválido" in invalid_err
        and missing == 1
        and "no encontrado" in missing_err
        and without_source == 2,
        "playbook add rechaza slug inválido, apoyo inexistente y falta de cuerpo",
    )

    _, listed = _json(["memory", "playbook", "list", "--json"])
    plain_code, plain_out, _ = _run(["memory", "playbook", "list"])
    h.ok(
        listed["count"] == 2
        and all(item["kind"] == "playbook" for item in listed["playbooks"])
        and plain_code == 0
        and "revisar-pr" in plain_out,
        "playbook list devuelve solo playbooks y los imprime",
    )

    _, shown = _json(["memory", "playbook", "show", "revisar-pr", "--json"])
    _, by_code = _json(["memory", "playbook", "show", created["code"], "--json"])
    h.ok(
        shown["code"] == created["code"]
        and by_code["id"] == created["id"]
        and shown["files"][0]["content"] == "- tests\n"
        and bool(shown["evidence"]),
        "playbook show resuelve por slug y código con apoyos y evidencia",
    )
    missing_show, _, err = _run(["memory", "playbook", "show", "no-existe"])
    _, detail_out, _ = _run(["memory", "playbook", "show", "revisar-pr"])
    h.ok(
        missing_show == 1
        and "no encontrado" in err
        and "Evidencia: ticket:T-0047" in detail_out
        and "references/checklist.md" in detail_out,
        "playbook show falla con un slug desconocido e imprime el detalle",
    )

    from commands import memory as memory_command

    original = memory_command.workspace.project_root
    memory_command.workspace.project_root = lambda: h.tmp
    try:
        _, exported = _json(
            ["memory", "playbook", "export", "revisar-pr", "--json"]
        )
        skill = Path(exported["path"])
        h.ok(
            skill.is_file()
            and 'name: "revisar-pr"' in skill.read_text(encoding="utf-8")
            and len(exported["files"]) == 3,
            "playbook export escribe SKILL.md con frontmatter y apoyos",
        )
        _, exported_all = _json(
            ["memory", "playbook", "export", "--all", "--json"]
        )
        plain_export, plain_export_out, _ = _run(
            ["memory", "playbook", "export", "--all"]
        )
        h.ok(
            exported_all["exported"] == ["desde-fichero", "revisar-pr"]
            and plain_export == 0
            and "Exportados 2 playbook(s)" in plain_export_out,
            "playbook export --all exporta los activos y resume",
        )
        no_ref, _, no_ref_err = _run(["memory", "playbook", "export"])
        both, _, both_err = _run(
            ["memory", "playbook", "export", "revisar-pr", "--all"]
        )
        h.ok(
            no_ref == 1
            and "Indica un slug" in no_ref_err
            and both == 1
            and "no ambos" in both_err,
            "playbook export exige slug o --all, nunca ambos",
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
    _, active = _json(["memory", "playbook", "list", "--json"])
    _, everything = _json(["memory", "playbook", "list", "--all", "--json"])
    h.ok(
        archived["status"] == "archived"
        and _json(["memory", "playbook", "show", "revisar-pr", "--json"])[1][
            "status"
        ]
        == "archived"
        and active["count"] == 1
        and everything["count"] == 2,
        "playbook archive archiva sin borrar y list distingue --all",
    )
    archive_out_code, archive_out, _ = _run(
        ["memory", "playbook", "archive", "desde-fichero", "--why", "obsoleto"]
    )
    h.ok(
        archive_out_code == 0 and "archivado (no borrado)" in archive_out,
        "playbook archive imprime el aviso de no borrado",
    )
