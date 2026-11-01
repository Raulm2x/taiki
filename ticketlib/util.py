"""Utilidades de validación y normalización."""

from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable

from .domain import ValidationError


def now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


# Agentes internos de OpenCode que no identifican al dueño real.
AGENT_ALIASES = {
    "build": "opencode",
    "plan": "opencode",
    "general": "opencode",
    "explore": "opencode",
    "summary": "opencode",
    "compaction": "opencode",
    "title": "opencode",
}


def normalize_agent(value: Any) -> str:
    """Nombre del agente dueño: ``build``/``plan`` son OpenCode, no otro."""
    text = str(value or "").strip()
    if not text:
        return text
    return AGENT_ALIASES.get(text.lower(), text)


def check_enum(value: Any, allowed: Iterable[str], field: str) -> None:
    if value is None:
        return
    allowed = tuple(allowed)
    if value not in allowed:
        raise ValidationError(
            f"{field} inválido: {value!r}. Valores permitidos: {', '.join(allowed)}"
        )


def as_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [item for item in re.split(r"[,\s]+", value) if item]
    return [str(item) for item in value]


def clean_tags(tags: Any) -> list[str]:
    out: list[str] = []
    for tag in as_list(tags):
        tag = tag.strip().lower()
        if tag and tag not in out:
            out.append(tag)
    return out


def normalize_code(value: Any) -> int | str:
    """Acepta T-0001, t-1 o 1. Devuelve código normalizado o id entero."""
    if isinstance(value, bool):
        raise ValidationError(f"Código de ticket inválido: {value!r}")
    if isinstance(value, int):
        return value
    text = str(value).strip()
    if not text:
        raise ValidationError("El código del ticket es obligatorio")
    if text.isdigit():
        return int(text)
    code = text.upper()
    if not code.startswith("T-"):
        code = f"T-{code}"
    if not re.fullmatch(r"T-\d+(?:\.\d+)?", code):
        raise ValidationError(f"Código de ticket inválido: {value!r} (esperado T-0001)")
    return code


TOKEN_RE = re.compile(r"[\wáéíóúüñÁÉÍÓÚÜÑ]+")


def search_tokens(query: str | None) -> list[str]:
    """Tokens normalizados de una búsqueda libre (minúsculas, sin signos)."""
    return TOKEN_RE.findall((query or "").lower())


def fts_match(query: str) -> str | None:
    """Convierte texto libre en una consulta FTS5 segura."""
    tokens = search_tokens(query)
    if not tokens:
        return None
    return " ".join(f'"{token}"*' for token in tokens)


def find_git_root(path: Path | str | None = None) -> Path | None:
    """Busca recursivamente hacia arriba la raíz de un repositorio Git."""
    if path is None:
        curr = Path.cwd().resolve()
    else:
        curr = Path(path).expanduser().resolve()
    if curr.is_file():
        curr = curr.parent
    for candidate in (curr, *curr.parents):
        try:
            if (candidate / ".git").exists():
                return candidate
        except OSError:
            continue
    return None


def resolve_scope(
    project: str | None, path: str | None
) -> tuple[str | None, str | None]:
    """Completa proyecto y ruta resolviendo a la raíz Git si existe."""
    base = Path(path).expanduser() if path else Path.cwd()
    if path is None:
        path = str(base)
    if project is None:
        git_root = find_git_root(base)
        if git_root is not None:
            project = git_root.name or None
        else:
            project = base.name or None
    return project, path
