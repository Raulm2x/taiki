"""Worktrees por tarea: aislamiento de escritura con una DB compartida.

Cada ticket que modifica código puede vivir en ``.worktrees/<T-XXXX>`` con su
propia rama ``agent/T-XXXX-<slug>``. El worktree comparte la historia de Git y
la base de datos de Taiki del checkout principal (``.taiki/taiki.db``), pero
tiene su propio índice y árbol de trabajo, así que dos sesiones no se pisan.
"""

from __future__ import annotations


import re
import shutil
import subprocess
import unicodedata
from pathlib import Path
from typing import Any

from . import env
from .domain import TicketError
from .util import now

DEFAULT_ROOT = ".worktrees"
IGNORE_LINE = ".worktrees/"
BOOTSTRAP_FILES = ("opencode.json",)
BOOTSTRAP_DIRS = (".opencode",)


def _git(root: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(root), *args],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        detail = result.stderr.strip() or f"git {' '.join(args)} falló"
        raise TicketError(detail)
    return result.stdout.strip()


def _git_ok(root: Path, *args: str) -> bool:
    result = subprocess.run(
        ["git", "-C", str(root), *args],
        capture_output=True,
        text=True,
    )
    return result.returncode == 0


def slugify(text: str, limit: int = 32) -> str:
    """Slug ASCII para el nombre de rama."""
    normalized = unicodedata.normalize("NFKD", text or "")
    ascii_text = normalized.encode("ascii", "ignore").decode()
    slug = re.sub(r"[^a-z0-9]+", "-", ascii_text.lower()).strip("-")
    return slug[:limit].strip("-")


def root_dir(main: Path) -> Path:
    """Directorio donde viven los worktrees (configurable por entorno)."""
    override = env.get("WORKTREE_ROOT")
    if override:
        return Path(override).expanduser()
    return main / DEFAULT_ROOT


def branch_name(code: str, title: str | None = None) -> str:
    slug = slugify(title or "")
    return f"agent/{code}-{slug}" if slug else f"agent/{code}"


def ensure_ignored(main: Path) -> Path:
    """Añade ``.worktrees/`` al ``.gitignore`` del checkout principal."""
    path = main / ".gitignore"
    content = path.read_text(encoding="utf-8") if path.is_file() else ""
    lines = content.splitlines()
    if IGNORE_LINE in lines:
        return path
    if content and not content.endswith("\n"):
        content += "\n"
    content += f"\n# Worktrees de agentes\n{IGNORE_LINE}\n"
    path.write_text(content, encoding="utf-8")
    return path


def _copy_bootstrap(main: Path, target: Path) -> list[str]:
    """Copia configuración local necesaria (nunca la DB ni secretos)."""
    copied: list[str] = []
    for name in BOOTSTRAP_FILES:
        source = main / name
        destination = target / name
        if source.is_file() and not destination.exists():
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, destination)
            copied.append(name)
    for name in BOOTSTRAP_DIRS:
        source = main / name
        destination = target / name
        if source.is_dir() and not destination.exists():
            shutil.copytree(source, destination)
            copied.append(name)
    return copied


def create(
    main: Path,
    code: str,
    *,
    title: str | None = None,
    branch: str | None = None,
    path: str | Path | None = None,
    start: str = "HEAD",
) -> dict[str, Any]:
    """Crea el worktree de un ticket y devuelve ruta, rama y bootstrap."""
    main = Path(main).expanduser().resolve()
    if not (main / ".git").exists() and not _git_ok(main, "rev-parse", "--git-dir"):
        raise TicketError(f"{main} no es un repositorio Git")
    ensure_ignored(main)
    target = (
        Path(path).expanduser().resolve()
        if path
        else (root_dir(main) / code).resolve()
    )
    if target.exists():
        raise TicketError(f"Ya existe el worktree {target}")
    branch = branch or branch_name(code, title)
    if _git_ok(main, "rev-parse", "--verify", f"refs/heads/{branch}"):
        raise TicketError(f"La rama {branch} ya existe; usa --branch")
    target.parent.mkdir(parents=True, exist_ok=True)
    _git(main, "worktree", "add", "-b", branch, str(target), start)
    copied = _copy_bootstrap(main, target)
    return {
        "code": code,
        "main": main,
        "path": target,
        "branch": branch,
        "bootstrap": copied,
        "created_at": now(),
    }


def list_worktrees(main: Path) -> list[dict[str, Any]]:
    """Worktrees del repositorio, incluido el principal."""
    main = Path(main).expanduser().resolve()
    output = _git(main, "worktree", "list", "--porcelain")
    items: list[dict[str, Any]] = []
    current: dict[str, Any] = {}
    for line in output.splitlines() + [""]:
        if not line.strip():
            if current:
                items.append(current)
                current = {}
            continue
        key, _, value = line.partition(" ")
        if key == "worktree":
            current["path"] = value
        elif key == "HEAD":
            current["head"] = value
        elif key == "branch":
            current["branch"] = value.removeprefix("refs/heads/")
        elif key == "detached":
            current["detached"] = True
        elif key == "bare":
            current["bare"] = True
    return items


def locate(main: Path, code: str) -> dict[str, Any] | None:
    """Busca el worktree de un ticket por ruta o por rama."""
    main = Path(main).expanduser().resolve()
    candidate = (root_dir(main) / code).resolve()
    for item in list_worktrees(main):
        path = Path(item.get("path", ""))
        if path == candidate or item.get("branch", "").startswith(f"agent/{code}-"):
            return item
        if item.get("branch") == f"agent/{code}":
            return item
    if candidate.exists():
        return {"path": str(candidate)}
    return None


def status(path: str | Path) -> dict[str, Any]:
    """Estado del worktree: cambios sin commitear y rama actual."""
    root = Path(path).expanduser()
    if not root.exists():
        return {"path": str(root), "exists": False, "dirty": False, "branch": None}
    result = subprocess.run(
        ["git", "-C", str(root), "status", "--porcelain"],
        capture_output=True,
        text=True,
    )
    branch = subprocess.run(
        ["git", "-C", str(root), "branch", "--show-current"],
        capture_output=True,
        text=True,
    )
    return {
        "path": str(root),
        "exists": True,
        "dirty": bool(result.stdout.strip()),
        "files": [line for line in result.stdout.splitlines() if line.strip()],
        "branch": (branch.stdout or "").strip() or None,
    }


def merged(main: Path, branch: str) -> bool:
    """¿La rama del worktree ya está contenida en la rama actual?"""
    main = Path(main).expanduser().resolve()
    if not branch:
        return True
    return _git_ok(main, "merge-base", "--is-ancestor", branch, "HEAD")


def warnings(main: Path, entries: list[dict[str, Any]]) -> list[str]:
    """Avisos antes de confirmar: worktree sucio, ausente o sin mergear."""
    messages: list[str] = []
    for entry in entries:
        path = entry.get("path")
        if not path:
            continue
        state = status(path)
        if not state["exists"]:
            messages.append(f"{entry.get('code', '?')}: worktree ausente ({path})")
            continue
        if state["dirty"]:
            messages.append(
                f"{entry.get('code', '?')}: worktree con cambios sin commitear "
                f"({len(state['files'])} fichero(s))"
            )
        branch = state["branch"] or entry.get("branch")
        if branch and branch.startswith("agent/") and not merged(main, branch):
            messages.append(
                f"{entry.get('code', '?')}: rama {branch} sin mergear en la actual"
            )
    return messages


def remove(
    main: Path,
    target: str | Path,
    *,
    force: bool = False,
    delete_branch: bool = False,
) -> dict[str, Any]:
    """Retira un worktree y, opcionalmente, su rama."""
    main = Path(main).expanduser().resolve()
    path = Path(target).expanduser().resolve()
    state = status(path)
    args = ["worktree", "remove"]
    if force:
        args.append("--force")
    args.append(str(path))
    _git(main, *args)
    removed: dict[str, Any] = {
        "path": str(path),
        "branch": state.get("branch"),
        "was_dirty": state.get("dirty"),
    }
    if delete_branch and state.get("branch"):
        _git(main, "branch", "-D", str(state["branch"]))
        removed["branch_deleted"] = state["branch"]
    return removed


def adopt(main: Path, path: str | Path) -> dict[str, Any]:
    """Valida un worktree existente (por ejemplo, creado por OpenCode)."""
    main = Path(main).expanduser().resolve()
    target = Path(path).expanduser().resolve()
    items = list_worktrees(main)
    match = next(
        (item for item in items if Path(item.get("path", "")).resolve() == target),
        None,
    )
    if match is None:
        raise TicketError(f"{target} no es un worktree de {main}")
    return {
        "path": str(target),
        "branch": match.get("branch"),
        "head": match.get("head"),
    }
