"""Resolución de commits de evidencia en varios repositorios."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Iterable

from .. import workspace
from ..domain import ValidationError
from ..gitutil import resolve_commit


def commit_roots(
    root: str | Path | None,
    repos: Iterable[Any] | None = None,
) -> list[Path]:
    """Repos donde buscar un commit: raíz, ``repos`` y dotfiles (repo/live)."""
    candidates: list[Any] = []
    if root is not None:
        candidates.append(root)
    for repo in repos or []:
        value = str(repo or "").strip()
        if value:
            candidates.append(value)
    try:
        settings = workspace.dotfiles_settings()
    except ValidationError:
        settings = {}
    if isinstance(settings, dict):
        candidates.extend(settings.get(key) for key in ("repo", "live"))
    roots: list[Path] = []
    for value in candidates:
        if not value:
            continue
        path = Path(str(value)).expanduser()
        if path not in roots:
            roots.append(path)
    return roots


def _explicit_repo(ref: str) -> tuple[Path, str] | None:
    """Separa ``repo@sha`` cuando la parte izquierda parece una ruta."""
    repo, sep, sha = ref.rpartition("@")
    repo = repo.strip()
    sha = sha.strip()
    if not sep or not repo or not sha:
        return None
    if not repo.startswith(("~", ".", "/")):
        return None
    return Path(repo).expanduser(), sha


def resolve_evidence_commit(
    ref: str,
    roots: list[Path],
    root: str | Path | None = None,
) -> str | None:
    """Hash del commit de una evidencia, o ``None`` si no resuelve.

    Acepta ``repo@sha`` con la ruta explícita; si es relativa se prueba también
    contra la raíz del proyecto. Un ref sin la forma ``ruta@sha`` se busca en
    cada raíz conocida.
    """
    explicit = _explicit_repo(ref)
    if explicit is not None:
        repo, sha = explicit
        found = resolve_commit(sha, repo)
        if found is None and root is not None and not repo.is_absolute():
            found = resolve_commit(sha, Path(root).expanduser() / repo)
        return found
    for item in roots:
        found = resolve_commit(ref, item)
        if found is not None:
            return found
    return None


