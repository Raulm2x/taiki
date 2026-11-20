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

from . import env, workspace
from .domain import TicketError
from .util import clean_path

PROTECTED_BRANCHES = {"main", "master"}
AGENT_PREFIX = "agent/"
HOTFIX_PREFIX = "hotfix/"
# Aislamientos que trabajan sobre el árbol vivo: el trabajo va en la rama
# configurada (``isolation.live_branch``) y no en una rama por ticket.
LIVE_ISOLATIONS = ("none", "scope")


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


def live_pattern(pattern: str, root: str | Path) -> str:
    """Traduce un patrón absoluto al root del repositorio live.

    Los tickets de dotfiles declaran rutas como ``~/.config/hypr/``, pero el
    índice de Git de ``taiki commit --repo ~/.config`` usa rutas relativas a
    ese repo (``hypr/...``). Un patrón absoluto (o con ``~``) que cae dentro
    de ``root`` se convierte a su ruta relativa posix; cualquier otro patrón
    (relativo, glob o fuera del repo) se conserva tal cual.
    """
    raw = str(pattern)
    candidate = Path(raw).expanduser()
    if not candidate.is_absolute():
        return raw
    try:
        resolved = candidate.resolve()
        return resolved.relative_to(Path(root).expanduser().resolve()).as_posix()
    except (OSError, ValueError):
        return raw


def _ignored(root: str | Path, patterns: Iterable[str]) -> list[str]:
    """Rutas del ticket que ``git check-ignore`` marca como ignoradas.

    Un ``paths`` que cae dentro de un patrón de ``.gitignore`` nunca llega al
    índice: ``git add`` lo salta en silencio y ``taiki commit`` acaba con
    "Nada en el índice" sin explicar por qué. El helper traduce los patrones
    absolutos al root live (``live_pattern``) y pregunta a Git por las rutas
    declaradas, sin depender de los globs. ``check-ignore`` sale con 1 cuando
    ninguna ruta está ignorada, así que aquí no se usa ``_git`` (lanza).
    """
    candidates = []
    for item in patterns or ():
        translated = live_pattern(str(item), root)
        if translated.strip():
            candidates.append(translated.strip())
    if not candidates:
        return []
    result = subprocess.run(
        ["git", "-C", str(root), "check-ignore", "--", *candidates],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        return []
    return sorted({line for line in result.stdout.splitlines() if line.strip()})


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


def in_worktree(root: str | Path = ".") -> bool:
    """¿El checkout es un worktree ligado (no el árbol principal)?"""
    try:
        git_dir = Path(
            _git(
                Path(root),
                "rev-parse",
                "--path-format=absolute",
                "--git-dir",
            ).strip()
        )
        common = Path(
            _git(
                Path(root),
                "rev-parse",
                "--path-format=absolute",
                "--git-common-dir",
            ).strip()
        )
    except TicketError:
        return False
    return git_dir != common


def branch_policy(
    branch: str | None,
    code: str | None,
    *,
    agent: bool = True,
    isolation: str | None = None,
    live_branch: str | None = None,
    worktree: bool = False,
) -> dict[str, Any]:
    """¿Puede esta rama recibir un commit del ticket ``code``?

    El flujo nuevo reserva ``main`` para ``taiki release``, exige ramas
    ``agent/T-XXXX-*`` para el trabajo diario y permite ``hotfix/T-XXXX``
    (creadas desde ``main``) únicamente con el ticket en el nombre. Un usuario
    que commitea fuera de esas ramas solo recibe un aviso; un agente se
    bloquea porque su trabajo siempre vive en una rama por ticket.

    Excepción live: con ``isolation`` ``none`` o ``scope`` el trabajo va sobre
    el árbol vivo, así que la rama configurada (``isolation.live_branch``,
    ``main`` por defecto) acepta commits de agente **con ticket**. Los
    worktrees ligados siguen bloqueando las ramas protegidas y ``git push``
    mantiene su propio gate.
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
        live = (
            isolation in LIVE_ISOLATIONS
            and bool(code)
            and bool(live_branch)
            and branch == live_branch
            and not worktree
        )
        if live:
            return result
        if agent:
            return {
                "ok": False,
                "reason": "protected-branch",
                "message": (
                    f"{branch} solo recibe releases: trabaja en una rama "
                    "agent/T-XXXX o usa un ticket con aislamiento live "
                    "(none/scope) sobre la rama configurada."
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
    isolation: str | None = None,
    live_branch: str | None = None,
    repo_root: str | Path | None = None,
) -> dict[str, Any]:
    """Analiza el índice contra el ticket, la rama y otras sesiones.

    ``isolation`` manda sobre los ``paths``:

    - ``worktree`` (default): solo se stagea lo que cae en ``paths``; sin
      ``paths`` no hay restricción (compatibilidad histórica).
    - ``scope``: ``paths`` es obligatorio. Sin ``paths`` se deniega todo
      (fail-closed, deuda B7): cualquier staged queda fuera de alcance.
    - ``none``: árbol vivo serial; no se exige ni se valida ``paths``.

    ``resources`` se acepta para documentar el alcance del ticket, pero no
    forma parte del índice de Git: no puede validarse aquí. El informe expone
    ``isolation``, ``scope_error`` y ``commit_scope`` además de las claves
    históricas.

    ``repo_root`` traduce los ``paths`` absolutos (``~/.config/hypr/``) al
    root del repositorio que se commitea cuando ``taiki commit --repo`` apunta
    a otro árbol (dotfiles). ``scope`` conserva los patrones originales del
    ticket y ``commit_scope`` refleja los usados para validar el índice.
    ``ignored`` lista las rutas del ticket que ``.gitignore`` descarta.
    """
    files = staged(root)
    name = current_branch(root) if branch is None else branch
    agent_mode = bool(env.get("AGENT")) if agent is None else agent
    settings = workspace.isolation_settings()
    mode = isolation or str(ticket.get("isolation") or settings["default"])
    live = live_branch or settings["live_branch"]
    linked = in_worktree(root)
    policy = branch_policy(
        name,
        ticket.get("code"),
        agent=agent_mode,
        isolation=mode,
        live_branch=live,
        worktree=linked,
    )
    scope = list(ticket.get("paths") or [])
    commit_scope = (
        [live_pattern(item, repo_root) for item in scope]
        if repo_root is not None
        else list(scope)
    )
    resources = list(ticket.get("resources") or [])
    ignored = _ignored(repo_root, commit_scope) if repo_root is not None else []
    scope_error: str | None = None
    if mode == "scope":
        if not scope:
            scope_error = (
                "isolation=scope sin paths: el ticket no puede commitear "
                "(fail-closed). Declara paths con `taiki update --paths ...`."
            )
            outside = list(files)
        else:
            outside = [item for item in files if not matches(item, commit_scope)]
    elif mode == "none":
        outside = []
    else:
        outside = [
            item for item in files if commit_scope and not matches(item, commit_scope)
        ]
    conflicts: list[dict[str, Any]] = []
    for other in others:
        if other.get("code") == ticket.get("code"):
            continue
        raw_patterns = list(other.get("paths") or [])
        if not raw_patterns:
            continue
        patterns = (
            [live_pattern(item, repo_root) for item in raw_patterns]
            if repo_root is not None
            else list(raw_patterns)
        )
        hits = [item for item in files if matches(item, patterns)]
        shared = overlaps(commit_scope, patterns)
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
        "isolation": mode,
        "worktree": linked,
        "scope": scope,
        "commit_scope": commit_scope,
        "resources": resources,
        "scope_error": scope_error,
        "staged": files,
        "outside": outside,
        "ignored": ignored,
        "conflicts": conflicts,
        "ok": (
            bool(files)
            and not outside
            and not scope_error
            and not conflicts
            and policy["ok"]
        ),
    }


def toplevel(root: str | Path) -> Path:
    """Raíz del repositorio Git en ``root`` (resuelve submódulos/rutas hijas)."""
    return Path(_git(Path(root), "rev-parse", "--show-toplevel").strip())


def commit(root: str | Path, message: str) -> str:
    """Crea el commit con el índice actual y devuelve el hash corto."""
    _git(Path(root), "commit", "-m", message)
    return _git(Path(root), "rev-parse", "--short", "HEAD").strip()
