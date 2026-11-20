"""Contrato del CLI de skills: suggest, install propuesto, list, remove y sync."""

from __future__ import annotations

import contextlib
import hashlib
import io
import json

import cli
from commands import skills as skills_command
from ticketlib.memory import create_playbook
from ticketlib.skills.registry import RAW_BASE_URL, bundle_hash
from tests.support import Harness

SKILLS = {
    "python-testing-patterns": (
        "---\n"
        "name: python-testing-patterns\n"
        "description: Patrones de pytest para el proyecto.\n"
        "---\n"
        "\n"
        "# Pytest\n"
        "\n"
        "Usa fixtures claras.\n"
    ),
    "sqlalchemy": (
        "---\n"
        "name: sqlalchemy\n"
        "description: Patrones de SQLAlchemy.\n"
        "---\n"
        "\n"
        "# SQLAlchemy\n"
        "\n"
        "Sesiones cortas.\n"
    ),
}


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _entry(name: str) -> dict:
    hashes = {"SKILL.md": _sha(SKILLS[name])}
    return {
        "source": "midudev/autoskills",
        "skillPath": f"midudev/autoskills/{name}",
        "commitSha": "0123456789abcdef0123456789abcdef01234567",
        "files": ["SKILL.md"],
        "sha256": hashes,
        "bundleHash": bundle_hash(hashes),
        "review": {"status": "approved", "flags": [], "summary": "ok"},
        "securityCheck": {"status": "ok", "findings": [], "summary": "ok"},
    }


def _registry() -> dict:
    return {"version": 1, "skills": {name: _entry(name) for name in SKILLS}}


def _fetcher():
    urls = {
        f"{RAW_BASE_URL}/{name}/SKILL.md": content
        for name, content in SKILLS.items()
    }

    def fetch(url: str) -> str:
        if url not in urls:
            raise AssertionError(f"URL inesperada: {url}")
        return urls[url]

    return fetch


def _run(argv: list[str]) -> tuple[int, str, str]:
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        try:
            code = cli.main(argv)
        except SystemExit as exc:
            code = int(exc.code or 0)
    return code, out.getvalue(), err.getvalue()


def _json(argv: list[str]) -> tuple[int, dict]:
    code, out, err = _run(argv)
    if code != 0:
        raise AssertionError(f"CLI falló ({code}): {err}")
    return code, json.loads(out)


class _NoTty:
    """Stdin no interactivo para el contrato de confirmación."""

    def isatty(self) -> bool:
        return False

    def read(self, *args, **kwargs) -> str:  # pragma: no cover - no se usa
        return ""


def run(h: Harness) -> None:
    root = h.tmp / "proyecto"
    root.mkdir()
    (root / "requirements.txt").write_text("pytest\nsqlalchemy\n", encoding="utf-8")
    registry_path = h.tmp / "index.json"
    registry_path.write_text(json.dumps(_registry()), encoding="utf-8")

    original_root = skills_command.workspace.project_root
    original_fetch = skills_command._fetch
    original_stdin = skills_command.sys.stdin
    skills_command.workspace.project_root = lambda: root
    skills_command._fetch = _fetcher()
    try:
        with h.env(TAIKI_SKILLS_REGISTRY=str(registry_path)):
            _, suggested = _json(["skills", "suggest", "--json"])
            h.eq(
                suggested["techs"],
                ["pytest", "python", "sqlalchemy"],
                "skills suggest detecta el stack del proyecto",
            )
            h.eq(
                [item["name"] for item in suggested["suggestions"]],
                ["python-testing-patterns", "sqlalchemy"],
                "skills suggest cruza el stack con el registro",
            )
            first = suggested["suggestions"][0]
            h.ok(
                first["source"] == "midudev/autoskills"
                and first["hash"] == _entry("python-testing-patterns")["bundleHash"]
                and first["review"]["status"] == "approved"
                and first["securityCheck"]["status"] == "ok",
                "cada sugerencia lleva fuente, hash, review y securityCheck",
            )

            plain, plain_out, _ = _run(["skills", "suggest"])
            h.ok(
                plain == 0
                and "Stack detectado: pytest, python, sqlalchemy" in plain_out
                and "python-testing-patterns" in plain_out,
                "skills suggest imprime el stack y las sugerencias",
            )
            bad_limit, _, err = _run(["skills", "suggest", "--limit", "0"])
            h.ok(
                bad_limit == 1 and "mayor que 0" in err,
                "skills suggest rechaza un límite no positivo",
            )

            skills_command.sys.stdin = _NoTty()
            no_yes, _, err = _run(["skills", "install", "python-testing-patterns"])
            skills_command.sys.stdin = original_stdin
            h.ok(
                no_yes == 1 and "--yes" in err,
                "install sin --yes falla en modo no interactivo",
            )

            _, installed = _json(
                ["skills", "install", "python-testing-patterns", "--yes", "--json"]
            )
            h.ok(
                installed["status"] == "proposed"
                and installed["source"] == "import"
                and installed["kind"] == "playbook"
                and installed["skill"]["source"] == "midudev/autoskills"
                and installed["code"].startswith("M-"),
                "install deja un playbook propuesto con código M- y procedencia",
            )
            plain_install, out, _ = _run(
                ["skills", "install", "sqlalchemy", "--yes"]
            )
            h.ok(
                plain_install == 0
                and "proposed" in out
                and "taiki memory approve M-" in out
                and "taiki memory reject M-" in out,
                "install imprime el código y las instrucciones de aprobación",
            )
            duplicate, _, err = _run(
                ["skills", "install", "python-testing-patterns", "--yes"]
            )
            h.ok(
                duplicate == 1 and "Ya existe" in err,
                "install no sobrescribe un playbook vigente",
            )

            _, listed = _json(["skills", "list", "--json"])
            h.ok(
                sorted(item["name"] for item in listed["skills"])
                == ["python-testing-patterns", "sqlalchemy"]
                and all(
                    item["origin"] == "autoskills" for item in listed["skills"]
                ),
                "skills list muestra las instaladas y marca el origen autoskills",
            )
            plain_list, list_out, _ = _run(["skills", "list"])
            h.ok(
                plain_list == 0 and "python-testing-patterns" in list_out,
                "skills list imprime las skills",
            )

            removed, remove_out, _ = _run(
                ["skills", "remove", "sqlalchemy", "--why", "No aplica."]
            )
            h.ok(
                removed == 0
                and "archivado (no borrado)" in remove_out,
                "skills remove archiva sin borrar",
            )
            _, after = _json(["skills", "list", "--json"])
            h.eq(
                [item["name"] for item in after["skills"]],
                ["python-testing-patterns"],
                "una skill archivada sale del listado",
            )

            create_playbook(
                h.conn,
                slug="activo-local",
                title="Activo local",
                description="Playbook activo para probar sync.",
                content="Paso uno.",
                evidence=[{"kind": "ticket", "ref": "T-0049.02"}],
                source="user",
            )
            _, synced = _json(["skills", "sync", "--json"])
            h.eq(
                synced["exported"],
                ["activo-local"],
                "skills sync exporta solo los playbooks activos",
            )
            h.ok(
                (root / ".taiki/generated/skills/activo-local/SKILL.md").is_file(),
                "skills sync escribe la skill en .taiki/generated/skills",
            )
            plain_sync, sync_out, _ = _run(["skills", "sync"])
            h.ok(
                plain_sync == 0 and "Exportados 1 playbook(s)" in sync_out,
                "skills sync imprime el resumen",
            )
    finally:
        skills_command.workspace.project_root = original_root
        skills_command._fetch = original_fetch
        skills_command.sys.stdin = original_stdin
