"""Detección de stack y catálogo de sugerencias, sin red ni procesos."""

from __future__ import annotations

from ticketlib.domain import ValidationError
from ticketlib.skills.catalog import TECH_SKILLS, normalize_tech, skills_for, suggest
from ticketlib.skills.detect import detect, detect_techs
from tests.support import Harness


def _write(root, name: str, content: str) -> None:
    path = root / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _python(h: Harness) -> None:
    root = h.tmp / "python"
    root.mkdir()
    _write(
        root,
        "requirements.txt",
        "fastapi\npydantic\nsqlalchemy\npandas\nnumpy\nscikit-learn\n"
        "celery\npytest\nrequests\ndjango\n",
    )
    h.eq(
        detect_techs(root),
        [
            "celery",
            "django",
            "fastapi",
            "numpy",
            "pandas",
            "pydantic",
            "pytest",
            "python",
            "requests",
            "scikit-learn",
            "sqlalchemy",
        ],
        "detecta Python y sus librerías desde requirements",
    )

    pyproject = h.tmp / "pyproject"
    pyproject.mkdir()
    _write(
        pyproject,
        "pyproject.toml",
        "[project]\ndependencies = ['flask>=3', 'SQLAlchemy>=2']\n",
    )
    h.eq(
        detect_techs(pyproject),
        ["flask", "python", "sqlalchemy"],
        "detecta desde pyproject y tolera mayúsculas",
    )

    pipfile = h.tmp / "pipfile"
    pipfile.mkdir()
    _write(pipfile, "Pipfile", "[packages]\nrequests = '*'\n")
    h.eq(detect_techs(pipfile), ["python", "requests"], "detecta desde Pipfile")

    setup = h.tmp / "setup"
    setup.mkdir()
    _write(setup, "setup.py", "from setuptools import setup\nsetup(name='x')\n")
    h.eq(detect_techs(setup), ["python"], "setup.py basta para detectar Python")


def _django(h: Harness) -> None:
    root = h.tmp / "django"
    root.mkdir()
    _write(root, "manage.py", "#!/usr/bin/env python\n")
    h.eq(detect_techs(root), ["django"], "manage.py detecta Django sin manifiesto")

    empty = h.tmp / "vacio"
    empty.mkdir()
    h.eq(detect_techs(empty), [], "un proyecto sin señales no detecta nada")
    h.eq(detect_techs(h.tmp / "no-existe"), [], "una raíz inexistente no falla")
    h.eq(detect(empty)["count"], 0, "detect resume la lista con count")


def _javascript(h: Harness) -> None:
    root = h.tmp / "js"
    root.mkdir()
    _write(
        root,
        "package.json",
        '{"dependencies": {"react": "19", "next": "15", "zod": "3"},'
        ' "devDependencies": {"typescript": "5", "vite": "6",'
        ' "vitest": "3", "@playwright/test": "1", "tailwindcss": "4"},'
        ' "peerDependencies": {"express": "5"}}',
    )
    h.eq(
        detect_techs(root),
        [
            "express",
            "nextjs",
            "node",
            "playwright",
            "react",
            "tailwind",
            "typescript",
            "vite",
            "vitest",
            "zod",
        ],
        "detecta paquetes JS de dependencies, devDependencies y peers",
    )

    bun = h.tmp / "bun"
    bun.mkdir()
    _write(bun, "bunfig.toml", "[install]\n")
    _write(bun, "package.json", '{"dependencies": {"hono": "4"}}')
    h.eq(detect_techs(bun), ["bun", "hono", "node"], "bunfig y package.json detectan Bun")

    node = h.tmp / "node"
    node.mkdir()
    _write(node, "package-lock.json", "{}")
    h.eq(detect_techs(node), ["node"], "un lockfile npm basta para Node")

    configs = h.tmp / "configs"
    configs.mkdir()
    for name in (
        "tsconfig.json",
        "angular.json",
        "vite.config.ts",
        "tailwind.config.js",
        "playwright.config.ts",
    ):
        _write(configs, name, "{}")
    h.eq(
        detect_techs(configs),
        ["angular", "playwright", "tailwind", "typescript", "vite"],
        "los ficheros de configuración también detectan tecnologías",
    )

    broken = h.tmp / "roto"
    broken.mkdir()
    _write(broken, "package.json", "no es json")
    h.eq(detect_techs(broken), ["node"], "un package.json inválido no rompe la detección")


def _registry() -> dict:
    def entry(name: str) -> dict:
        return {
            "source": "midudev/autoskills",
            "skillPath": f"midudev/autoskills/{name}",
            "commitSha": "0123456789abcdef0123456789abcdef01234567",
            "files": ["SKILL.md"],
            "sha256": {"SKILL.md": "0" * 64},
            "bundleHash": "1" * 64,
            "review": {"status": "approved", "flags": [], "summary": "ok"},
            "securityCheck": {"status": "ok", "findings": [], "summary": "ok"},
        }

    names = (
        "python-testing-patterns",
        "sqlalchemy",
        "pandas-pro",
        "scikit-learn",
    )
    return {"version": 1, "skills": {name: entry(name) for name in names}}


def _catalog(h: Harness) -> None:
    registry = _registry()
    suggestions = suggest(["pytest", "sqlalchemy", "pandas"], registry)
    h.eq(
        [item["name"] for item in suggestions],
        ["python-testing-patterns", "sqlalchemy", "pandas-pro"],
        "suggest traduce tecnologías a skills presentes en el registro",
    )
    first = suggestions[0]
    h.eq(first["source"], "midudev/autoskills", "cada sugerencia lleva source")
    h.eq(first["hash"], "1" * 64, "cada sugerencia lleva el bundleHash")
    h.eq(first["review"]["status"], "approved", "cada sugerencia lleva review")
    h.eq(first["securityCheck"]["status"], "ok", "cada sugerencia lleva securityCheck")

    deduped = suggest(["python", "pytest"], registry)
    h.eq(
        [item["name"] for item in deduped],
        ["python-testing-patterns"],
        "una skill compartida por dos tecnologías aparece una vez",
    )
    h.eq(
        deduped[0]["techs"],
        ["python", "pytest"],
        "la sugerencia agrega las tecnologías que la justifican",
    )

    alias = suggest(["sklearn"], registry)
    h.eq(alias[0]["name"], "scikit-learn", "el alias sklearn llega al registro")
    h.eq(normalize_tech("Node.js"), "node", "normalize_tech resuelve alias comunes")
    h.eq(skills_for("sklearn")[0], "scikit-learn", "skills_for usa el alias")
    h.eq(suggest(["no-existe"], registry), [], "una tecnología desconocida no sugiere")
    h.eq(
        [item["name"] for item in suggest(["python", "pytest"], registry, limit=0)],
        ["python-testing-patterns"],
        "limit=0 devuelve todas las sugerencias",
    )
    h.eq(len(suggest(["python", "sqlalchemy"], registry, limit=1)), 1, "limit acota")
    h.raises(
        ValidationError,
        lambda: suggest(["python"], {}, limit=-1),
        "limit negativo es un error",
    )
    h.raises(
        ValidationError,
        lambda: suggest(["python"], {"version": 1}),
        "un registro sin skills es un error",
    )
    h.ok(
        all(tech in TECH_SKILLS for tech in ("python", "pytest", "django", "react")),
        "TECH_SKILLS cubre las tecnologías del ticket",
    )


def run(h: Harness) -> None:
    _python(h)
    _django(h)
    _javascript(h)
    _catalog(h)
