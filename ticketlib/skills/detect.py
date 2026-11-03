"""Detección local del stack del proyecto, sin red ni procesos externos.

Lee únicamente manifiestos y ficheros de configuración del directorio raíz:
``pyproject.toml``, ``requirements*.txt``, ``setup.py``/``setup.cfg``,
``Pipfile``, ``manage.py`` y ``package.json`` (más lockfiles y configs
habituales). Devuelve identificadores canónicos que ``catalog`` traduce a
skills del registro de AutoSkills.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

PYTHON_FILES = (
    "pyproject.toml",
    "requirements.txt",
    "setup.py",
    "setup.cfg",
    "Pipfile",
)
PYTHON_SIGNALS: dict[str, tuple[str, ...]] = {
    "pytest": ("pytest",),
    "requests": ("requests",),
    "fastapi": ("fastapi",),
    "django": ("django",),
    "flask": ("flask",),
    "pydantic": ("pydantic",),
    "sqlalchemy": ("sqlalchemy",),
    "pandas": ("pandas",),
    "numpy": ("numpy",),
    "scikit-learn": ("scikit-learn", "scikit_learn", "sklearn"),
    "celery": ("celery",),
}

JS_PACKAGES: dict[str, str] = {
    "react": "react",
    "react-dom": "react",
    "next": "nextjs",
    "vue": "vue",
    "nuxt": "nuxt",
    "pinia": "pinia",
    "svelte": "svelte",
    "@sveltejs/kit": "svelte",
    "@angular/core": "angular",
    "astro": "astro",
    "tailwindcss": "tailwind",
    "@tailwindcss/vite": "tailwind",
    "typescript": "typescript",
    "vite": "vite",
    "vitest": "vitest",
    "@playwright/test": "playwright",
    "playwright": "playwright",
    "express": "express",
    "hono": "hono",
    "@nestjs/core": "nestjs",
    "prisma": "prisma",
    "@prisma/client": "prisma",
    "drizzle-orm": "drizzle",
    "drizzle-kit": "drizzle",
    "zod": "zod",
    "three": "threejs",
    "gsap": "gsap",
    "stripe": "stripe",
    "@stripe/stripe-js": "stripe",
    "better-auth": "better-auth",
}

JS_LOCKFILES = ("package-lock.json", "yarn.lock", "pnpm-lock.yaml")
BUN_FILES = ("bun.lock", "bun.lockb", "bunfig.toml")
JS_CONFIG_FILES: dict[str, str] = {
    "tsconfig.json": "typescript",
    "angular.json": "angular",
    "svelte.config.js": "svelte",
    "components.json": "shadcn",
    "bunfig.toml": "bun",
}
JS_CONFIG_GLOBS: dict[str, str] = {
    "next.config.*": "nextjs",
    "nuxt.config.*": "nuxt",
    "astro.config.*": "astro",
    "vite.config.*": "vite",
    "vitest.config.*": "vitest",
    "playwright.config.*": "playwright",
    "tailwind.config.*": "tailwind",
}


def _read_text(path: Path, limit: int = 200_000) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="ignore")[:limit]
    except OSError:
        return ""


def _has_token(text: str, token: str) -> bool:
    """Busca un token de dependencia con límites tolerantes a ``-``/``_``."""
    normalized = text.lower().replace("_", "-")
    pattern = r"(?<![a-z0-9])" + re.escape(token.lower()) + r"(?![a-z0-9])"
    return re.search(pattern, normalized) is not None


def _python_files(root: Path) -> list[Path]:
    files = [root / name for name in PYTHON_FILES if (root / name).is_file()]
    files.extend(sorted(root.glob("requirements*.txt")))
    return files


def _python_techs(root: Path) -> set[str]:
    files = _python_files(root)
    techs: set[str] = set()
    if files:
        techs.add("python")
    text = "\n".join(_read_text(path) for path in files)
    for tech, tokens in PYTHON_SIGNALS.items():
        if any(_has_token(text, token) for token in tokens):
            techs.add(tech)
    if (root / "manage.py").is_file():
        techs.add("django")
    return techs


def _package_json(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _js_dependencies(data: dict[str, Any]) -> set[str]:
    names: set[str] = set()
    for section in ("dependencies", "devDependencies", "peerDependencies"):
        values = data.get(section)
        if isinstance(values, dict):
            names.update(str(key) for key in values)
    return names


def _js_techs(root: Path) -> set[str]:
    techs: set[str] = set()
    package = root / "package.json"
    if package.is_file():
        techs.add("node")
        for dependency in _js_dependencies(_package_json(package)):
            tech = JS_PACKAGES.get(dependency)
            if tech is not None:
                techs.add(tech)
    if any((root / name).is_file() for name in JS_LOCKFILES):
        techs.add("node")
    if any((root / name).is_file() for name in BUN_FILES):
        techs.add("bun")
    for name, tech in JS_CONFIG_FILES.items():
        if (root / name).is_file():
            techs.add(tech)
    for pattern, tech in JS_CONFIG_GLOBS.items():
        if any(root.glob(pattern)):
            techs.add(tech)
    return techs


def detect_techs(root: str | Path) -> list[str]:
    """Devuelve los identificadores de tecnología detectados en ``root``.

    El orden es determinista (alfabético) para que las sugerencias y los tests
    no dependan del orden de lectura del sistema de ficheros.
    """
    base = Path(root).expanduser()
    if not base.is_dir():
        return []
    return sorted(_python_techs(base) | _js_techs(base))


def detect(root: str | Path) -> dict[str, Any]:
    """Resultado de detección con la raíz y la lista de tecnologías."""
    base = Path(root).expanduser()
    techs = detect_techs(base)
    return {"root": str(base), "techs": techs, "count": len(techs)}


__all__ = [
    "BUN_FILES",
    "JS_CONFIG_FILES",
    "JS_CONFIG_GLOBS",
    "JS_LOCKFILES",
    "JS_PACKAGES",
    "PYTHON_FILES",
    "PYTHON_SIGNALS",
    "detect",
    "detect_techs",
]
