"""Catálogo de AutoSkills: traducción de tecnologías detectadas a skills.

``TECH_SKILLS`` usa los nombres cortos del registro (último segmento de
``skillPath``). ``suggest`` cruza las tecnologías del proyecto con el registro
y devuelve solo entradas presentes, con fuente, hash, revisión y comprobación
de seguridad para que la capa superior decida.
"""

from __future__ import annotations

from typing import Any, Iterable

from ..domain import ValidationError

TECH_SKILLS: dict[str, tuple[str, ...]] = {
    # Python y datos
    "python": ("python-executor", "python-testing-patterns"),
    "pytest": ("python-testing-patterns",),
    "requests": ("python-patterns",),
    "fastapi": ("fastapi", "fastapi-templates", "fastapi-python"),
    "fastmcp": ("fastmcp",),
    "django": ("django-expert", "django-patterns", "django-security"),
    "flask": ("flask-api-development",),
    "pydantic": ("pydantic",),
    "sqlalchemy": (
        "sqlalchemy",
        "sqlalchemy-alembic-expert-best-practices-code-review",
    ),
    "pandas": ("pandas-pro", "pandas-data-analysis"),
    "numpy": ("machine-learning", "pandas-data-analysis"),
    "scikit-learn": ("scikit-learn", "senior-data-scientist"),
    "celery": ("python-background-jobs",),
    # JavaScript / TypeScript
    "react": ("react-best-practices", "composition-patterns"),
    "nextjs": ("next-cache-components-adoption",),
    "vue": ("vue-debug-guides", "vue", "vue-best-practices"),
    "nuxt": ("nuxt",),
    "pinia": ("vue-pinia-best-practices",),
    "svelte": ("svelte5-best-practices", "svelte-code-writer"),
    "angular": (
        "angular-developer",
        "reference-core",
        "reference-signal-forms",
        "reference-compiler-cli",
        "adev-writing-guide",
    ),
    "astro": ("astro",),
    "tailwind": ("tailwind-css-patterns",),
    "shadcn": ("shadcn",),
    "typescript": ("typescript-advanced-types",),
    "vite": ("vite",),
    "vitest": ("vitest",),
    "playwright": ("playwright-best-practices",),
    "node": ("nodejs-backend-patterns", "nodejs-best-practices"),
    "express": ("nodejs-express-server",),
    "hono": ("hono",),
    "nestjs": ("nestjs-best-practices",),
    "prisma": (
        "prisma-database-setup",
        "prisma-client-api",
        "prisma-cli",
        "prisma-postgres",
    ),
    "drizzle": ("drizzle",),
    "zod": ("zod",),
    "threejs": ("threejs-fundamentals",),
    "gsap": ("gsap-core",),
    "bun": ("bun",),
    "stripe": ("stripe-best-practices", "upgrade-stripe"),
    "better-auth": ("best-practices",),
}

TECH_ALIASES: dict[str, str] = {
    "scikit_learn": "scikit-learn",
    "sklearn": "scikit-learn",
    "next": "nextjs",
    "next.js": "nextjs",
    "nodejs": "node",
    "node.js": "node",
    "react-native": "react-native",
}

DEFAULT_LIMIT = 20


def normalize_tech(tech: object) -> str:
    """Normaliza un identificador de tecnología a la clave del catálogo."""
    text = str(tech if tech is not None else "").strip().lower()
    return TECH_ALIASES.get(text, text)


def skills_for(tech: object) -> tuple[str, ...]:
    """Nombres de skills asociados a una tecnología (vacío si no hay)."""
    return TECH_SKILLS.get(normalize_tech(tech), ())


def _review(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def suggest(
    techs: Iterable[object],
    registry: dict[str, Any],
    *,
    limit: int = DEFAULT_LIMIT,
) -> list[dict[str, Any]]:
    """Cruza las tecnologías con el registro y sugiere skills presentes.

    No filtra por estado de revisión: devuelve ``review`` y ``securityCheck``
    completos para que el conductor decida. Deduplica por nombre y conserva el
    orden de las tecnologías y de ``TECH_SKILLS``.
    """
    if limit < 0:
        raise ValidationError("limit debe ser cero o positivo")
    skills = registry.get("skills")
    if not isinstance(skills, dict):
        raise ValidationError("El registro de skills no contiene 'skills'")

    wanted: dict[str, list[str]] = {}
    for tech in techs:
        key = normalize_tech(tech)
        for name in TECH_SKILLS.get(key, ()):
            wanted.setdefault(name, []).append(key)

    out: list[dict[str, Any]] = []
    for name, tech_names in wanted.items():
        entry = skills.get(name)
        if not isinstance(entry, dict):
            continue
        review = _review(entry.get("review"))
        security = _review(entry.get("securityCheck"))
        out.append(
            {
                "name": name,
                "techs": tech_names,
                "source": entry.get("source"),
                "skillPath": entry.get("skillPath"),
                "commitSha": entry.get("commitSha"),
                "hash": entry.get("bundleHash"),
                "bundleHash": entry.get("bundleHash"),
                "files": list(entry.get("files") or []),
                "review": {
                    "status": review.get("status"),
                    "flags": list(review.get("flags") or []),
                    "summary": review.get("summary"),
                },
                "securityCheck": {
                    "status": security.get("status"),
                    "findings": list(security.get("findings") or []),
                    "summary": security.get("summary"),
                },
            }
        )
        if limit and len(out) >= int(limit):
            break
    return out


__all__ = [
    "DEFAULT_LIMIT",
    "TECH_ALIASES",
    "TECH_SKILLS",
    "normalize_tech",
    "skills_for",
    "suggest",
]
