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

from . import env
from .domain import TicketError
from .util import clean_path

PROTECTED_BRANCHES = {"main", "master"}
AGENT_PREFIX = "agent/"
HOTFIX_PREFIX = "hotfix/"


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
        pattern = clean_path(raw)
        if not pattern:
            continue
        if fnmatch.fnmatch(path, pattern):
            return True
        if path == pattern or path.startswith(pattern.rstrip("/") + "/"):
            return True
    return False


def _prefix(pattern: str) -> str:
    """Prefijo literal de un glob, para detectar solapamientos aproximados."""
    pattern = clean_path(pattern).rstrip("/")
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


def current_branch(root: str | Path = ".") -> str | None:
    """Rama actual del checkout (``None`` si hay HEAD desacoplado o no hay git)."""
    try:
        return _git(Path(root), "branch", "--show-current").strip() or None
    except TicketError:
        return None


def branch_policy(
    branch: str | None, code: str | None, *, agent: bool = True
) -> dict[str, Any]:
    """¿Puede esta rama recibir un commit del ticket ``code``?

    El flujo nuevo reserva ``main`` para ``taiki release``, exige ramas
    ``agent/T-XXXX-*`` para el trabajo diario y permite ``hotfix/T-XXXX``
    (creadas desde ``main``) únicamente con el ticket en el nombre. Un usuario
    que commitea fuera de esas ramas solo recibe un aviso; un agente se
    bloquea porque su trabajo siempre vive en una rama por ticket.
    """
    result: dict[str, Any] = {
        "ok": True,
        "reason": None,
        "message": "",
        "warning": None,
    }
    if not branch or branch == "HEAD":
        return result
    if branch in PROTECTED_BRANCHES:
        if agent:
            return {
                "ok": False,
                "reason": "protected-branch",
                "message": (
                    f"{branch} solo recibe releases: trabaja en una rama "
                    "agent/T-XXXX y publica con `taiki release`."
                ),
                "warning": None,
            }
        result["warning"] = (
            f"{branch} solo debería recibir releases de `taiki release`"
        )
        return result
    if branch.startswith(AGENT_PREFIX):
        expected = f"{AGENT_PREFIX}{code}" if code else ""
        if expected and (branch == expected or branch.startswith(expected + "-")):
            return result
        return {
            "ok": False,
            "reason": "branch-mismatch",
            "message": (
                f"la rama {branch} no corresponde a {code or 'ningún ticket'}; "
                "usa agent/T-XXXX-*"
            ),
            "warning": None,
        }
    if branch.startswith(HOTFIX_PREFIX):
        if code and code in branch:
            return result
        return {
            "ok": False,
            "reason": "branch-mismatch",
            "message": (
                f"{branch} debe incluir el ticket: hotfix/{code or 'T-XXXX'}"
            ),
            "warning": None,
        }
    if agent:
        return {
            "ok": False,
            "reason": "branch",
            "message": (
                f"{branch} no es agent/T-XXXX-*; el trabajo diario va en una "
                "rama por ticket"
            ),
            "warning": None,
        }
    result["warning"] = f"{branch} no es agent/T-XXXX-*; integra por develop"
    return result


def check(
    root: str | Path,
    ticket: dict[str, Any],
    others: Iterable[dict[str, Any]] = (),
    *,
    branch: str | None = None,
    agent: bool | None = None,
) -> dict[str, Any]:
    """Analiza el índice contra el ticket, la rama y otras sesiones."""
    files = staged(root)
    name = current_branch(root) if branch is None else branch
    agent_mode = bool(env.get("AGENT")) if agent is None else agent
    policy = branch_policy(name, ticket.get("code"), agent=agent_mode)
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
        "branch": name,
        "branch_reason": policy["reason"],
        "branch_message": policy["message"],
        "scope": scope,
        "staged": files,
        "outside": outside,
        "conflicts": conflicts,
        "ok": (
            bool(files)
            and not outside
            and not conflicts
            and policy["ok"]
        ),
    }


def commit(root: str | Path, message: str) -> str:
    """Crea el commit con el índice actual y devuelve el hash corto."""
    _git(Path(root), "commit", "-m", message)
    return _git(Path(root), "rev-parse", "--short", "HEAD").strip()
