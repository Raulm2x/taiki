"""Commit seguro: solo las rutas que pertenecen al ticket activo.

Con varias sesiones sobre el mismo repositorio, ``git add -A`` mezcla trabajo
ajeno. Estas funciones calculan qué hay en el índice, comparan con el alcance
declarado del ticket (``paths``) y con los tickets activos de otras sesiones, y
solo entonces permiten commitear.
"""

from __future__ import annotations

import fnmatch
import re
import subprocess
from pathlib import Path
from typing import Any, Iterable

from .domain import TicketError


def _git(root: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(root), *args],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        detail = result.stderr.strip() or f"git {' '.join(args)} falló"
        raise TicketError(detail)
    return result.stdout


def staged(root: str | Path) -> list[str]:
    """Ficheros en el índice, en rutas relativas a la raíz del repo."""
    output = _git(Path(root), "diff", "--cached", "--name-only")
    return [line for line in output.splitlines() if line.strip()]


def unstaged(root: str | Path) -> list[str]:
    """Cambios sin stagear o sin seguimiento (posible WIP de otra sesión)."""
    output = _git(Path(root), "status", "--porcelain")
    files: list[str] = []
    for line in output.splitlines():
        if len(line) < 4:
            continue
        code = line[:2]
        if code[0] != " " and code[0] != "?":
            continue  # ya está en el índice
        files.append(line[3:].strip())
    return files


def matches(path: str, patterns: Iterable[str]) -> bool:
    """¿La ruta cae dentro de alguno de los globs/directorios del ticket?"""
    for raw in patterns or ():
        pattern = str(raw or "").strip().lstrip("./")
        if not pattern:
            continue
        if fnmatch.fnmatch(path, pattern):
            return True
        if path == pattern or path.startswith(pattern.rstrip("/") + "/"):
            return True
    return False


def _prefix(pattern: str) -> str:
    """Prefijo literal de un glob, para detectar solapamientos aproximados."""
    pattern = str(pattern or "").strip().lstrip("./").rstrip("/")
    cut = re.split(r"[*?\[]", pattern, maxsplit=1)[0]
    return cut.rstrip("/")


def overlaps(left: Iterable[str], right: Iterable[str]) -> list[str]:
    """Pares de patrones que podrían tocar los mismos ficheros."""
    hits: list[str] = []
    for a in left or ():
        for b in right or ():
            pa, pb = _prefix(str(a)), _prefix(str(b))
            if not pa or not pb:
                continue
            if (
                pa == pb
                or pa.startswith(pb + "/")
                or pb.startswith(pa + "/")
                or fnmatch.fnmatch(pa, str(b))
                or fnmatch.fnmatch(pb, str(a))
            ):
                hits.append(f"{a} ~ {b}")
    return hits


def check(
    root: str | Path,
    ticket: dict[str, Any],
    others: Iterable[dict[str, Any]] = (),
) -> dict[str, Any]:
    """Analiza el índice contra el alcance del ticket y de otras sesiones."""
    files = staged(root)
    scope = list(ticket.get("paths") or [])
    outside = [item for item in files if scope and not matches(item, scope)]
    conflicts: list[dict[str, Any]] = []
    for other in others:
        if other.get("code") == ticket.get("code"):
            continue
        patterns = list(other.get("paths") or [])
        if not patterns:
            continue
        hits = [item for item in files if matches(item, patterns)]
        shared = overlaps(scope, patterns)
        if hits or shared:
            conflicts.append(
                {
                    "ticket": other["code"],
                    "files": hits,
                    "overlap": shared,
                }
            )
    return {
        "ticket": ticket.get("code"),
        "scope": scope,
        "staged": files,
        "outside": outside,
        "conflicts": conflicts,
        "ok": bool(files) and not outside and not conflicts,
    }


def commit(root: str | Path, message: str) -> str:
    """Crea el commit con el índice actual y devuelve el hash corto."""
    _git(Path(root), "commit", "-m", message)
    return _git(Path(root), "rev-parse", "--short", "HEAD").strip()
