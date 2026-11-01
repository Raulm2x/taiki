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

from . import env, workspace
from .domain import TicketError
from .paths import MARKER
from .util import now

DEFAULT_ROOT = ".worktrees"
IGNORE_LINE = ".worktrees/"
DEFAULT_INTEGRATION_BRANCH = "develop"
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


def configured_integration_branch(config: dict[str, Any] | None = None) -> str:
    """Rama configurada en ``orchestrator.integration_branch`` (o ``develop``)."""
    raw = (config or {}).get("orchestrator")
    value = raw.get("integration_branch") if isinstance(raw, dict) else None
    if isinstance(value, str) and value.strip():
        return value.strip()
    return DEFAULT_INTEGRATION_BRANCH


def _config(main: Path) -> dict[str, Any]:
    return workspace.load_config(main / MARKER)


def integration_ref(main: Path, config: dict[str, Any] | None = None) -> dict[str, Any]:
    """Rama donde se integra el trabajo diario y aviso si no existe.

    Si la rama configurada no está en el repositorio se usa la rama actual con
    un aviso, nunca un error: los repos sin ``develop`` siguen funcionando.
    """
    main = Path(main).expanduser().resolve()
    config = _config(main) if config is None else config
    wanted = configured_integration_branch(config)
    if _git_ok(main, "rev-parse", "--verify", f"refs/heads/{wanted}"):
        return {
            "branch": wanted,
            "configured": wanted,
            "fallback": False,
            "warning": None,
        }
    current = status(main).get("branch") or wanted
    warning = f"no existe {wanted}; se usa {current}"
    return {
        "branch": current,
        "configured": wanted,
        "fallback": True,
        "warning": warning,
    }


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
    start: str | None = None,
) -> dict[str, Any]:
    """Crea el worktree de un ticket y devuelve ruta, rama y bootstrap.

    La rama ``agent/…`` nace de la rama de integración (``develop`` por
    defecto) salvo que se indique ``start``.
    """
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
    ref = integration_ref(main)
    base = start or ref["branch"]
    target.parent.mkdir(parents=True, exist_ok=True)
    _git(main, "worktree", "add", "-b", branch, str(target), base)
    copied = _copy_bootstrap(main, target)
    return {
        "code": code,
        "main": main,
        "path": target,
        "branch": branch,
        "start": base,
        "integration_branch": ref["branch"],
        "warning": ref["warning"],
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


def merged(main: Path, branch: str, *, integration: str | None = None) -> bool:
    """¿La rama del worktree ya está contenida en la rama de integración?"""
    main = Path(main).expanduser().resolve()
    if not branch:
        return True
    target = integration or integration_ref(main)["branch"]
    return _git_ok(main, "merge-base", "--is-ancestor", branch, target)


def warnings(
    main: Path,
    entries: list[dict[str, Any]],
    *,
    config: dict[str, Any] | None = None,
) -> list[str]:
    """Avisos antes de confirmar: worktree sucio, ausente o sin mergear."""
    main = Path(main).expanduser().resolve()
    ref = integration_ref(main, config)
    messages: list[str] = []
    if entries and ref["warning"]:
        messages.append(f"integración: {ref['warning']}")
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
        if (
            branch
            and branch.startswith("agent/")
            and not merged(main, branch, integration=ref["branch"])
        ):
            messages.append(
                f"{entry.get('code', '?')}: rama {branch} sin mergear "
                f"en {ref['branch']}"
            )
    return messages


def cleanup(
    main: Path,
    entries: list[dict[str, Any]],
    *,
    keep: bool = False,
    config: dict[str, Any] | None = None,
) -> dict[str, list[dict[str, Any]]]:
    """Retira worktrees limpios y mergeados; conserva sucios o sin mergear."""
    main = Path(main).expanduser().resolve()
    integration = integration_ref(main, config)["branch"]
    removed: list[dict[str, Any]] = []
    kept: list[dict[str, Any]] = []
    for entry in entries:
        path = entry.get("path")
        code = entry.get("code")
        if not path:
            continue
        state = status(path)
        branch = state.get("branch") or entry.get("branch")
        if not state["exists"]:
            removed.append({"code": code, "path": path, "reason": "ausente"})
            continue
        unmerged = bool(
            branch
            and branch.startswith("agent/")
            and not merged(main, branch, integration=integration)
        )
        if keep or state["dirty"] or unmerged:
            kept.append(
                {
                    "code": code,
                    "path": path,
                    "branch": branch,
                    "dirty": state["dirty"],
                    "unmerged": unmerged,
                }
            )
            continue
        try:
            remove(main, path, delete_branch=bool(branch))
            removed.append({"code": code, "path": path, "branch": branch})
        except TicketError as exc:
            kept.append(
                {
                    "code": code,
                    "path": path,
                    "branch": branch,
                    "dirty": state["dirty"],
                    "error": str(exc),
                }
            )
    return {"removed": removed, "kept": kept}


def _still_registered(main: Path, path: Path) -> bool:
    """¿Sigue la ruta en ``git worktree list``?"""
    for item in list_worktrees(main):
        if Path(item.get("path", "")).resolve() == path:
            return True
    return False


def remove(
    main: Path,
    target: str | Path,
    *,
    force: bool = False,
    delete_branch: bool = False,
) -> dict[str, Any]:
    """Retira un worktree y, opcionalmente, su rama.

    Verifica que el directorio y el registro de Git desaparecieron de verdad;
    si alguno sigue ahí, lanza ``TicketError`` en vez de dar la retirada por
    hecha. ``removed`` solo es ``True`` cuando la comprobación pasa.
    """
    main = Path(main).expanduser().resolve()
    path = Path(target).expanduser().resolve()
    state = status(path)
    args = ["worktree", "remove"]
    if force:
        args.append("--force")
    args.append(str(path))
    try:
        _git(main, *args)
    except TicketError:
        # Un directorio borrado a mano deja un registro obsoleto: prune lo limpia.
        if path.exists():
            raise
        _git(main, "worktree", "prune")
        if _still_registered(main, path):
            raise
    if path.exists() or _still_registered(main, path):
        _git(main, "worktree", "prune")
        if path.exists() or _still_registered(main, path):
            raise TicketError(
                f"El worktree {path} sigue presente tras `git worktree remove`; "
                "revisa procesos que lo usen y retíralo a mano con "
                f"`git worktree remove --force {path}`."
            )
    removed: dict[str, Any] = {
        "path": str(path),
        "branch": state.get("branch"),
        "was_dirty": state.get("dirty"),
        "removed": True,
    }
    if delete_branch and state.get("branch"):
        _git(main, "branch", "-D", str(state["branch"]))
        removed["branch_deleted"] = state["branch"]
    return removed


def _tracked_dirty(root: Path) -> bool:
    result = subprocess.run(
        ["git", "-C", str(root), "status", "--porcelain", "--untracked-files=no"],
        capture_output=True,
        text=True,
    )
    return bool(result.stdout.strip())


def merge(
    main: Path,
    target: str | Path,
    *,
    test_cmd: list[str] | None = None,
    delete: bool = False,
) -> dict[str, Any]:
    """Rebase + tests + merge ff-only en la rama de integración.

    La rama del worktree se rebasa sobre ``orchestrator.integration_branch``
    (``develop`` por defecto) y se integra ahí, no en la rama actual. Si la
    rama configurada no existe se usa la actual con un aviso. Exige el
    worktree y el checkout principal limpios; si el rebase o los tests fallan,
    no se mergea nada.
    """
    main = Path(main).expanduser().resolve()
    path = Path(target).expanduser().resolve()
    tree = status(path)
    if not tree["exists"]:
        raise TicketError(f"No existe el worktree {path}")
    if tree["dirty"]:
        raise TicketError(f"{path} tiene cambios sin commitear")
    branch = tree.get("branch")
    if not branch or not branch.startswith("agent/"):
        raise TicketError(f"{path} no está en una rama agent/…")
    base = status(main)
    if _tracked_dirty(main):
        raise TicketError(f"El checkout principal {main} tiene cambios sin commitear")
    checkout_branch = base.get("branch")
    if not checkout_branch:
        raise TicketError("El checkout principal no está en una rama")
    ref = integration_ref(main)
    integration = ref["branch"]
    try:
        _git(path, "rebase", integration)
    except TicketError:
        subprocess.run(
            ["git", "-C", str(path), "rebase", "--abort"],
            capture_output=True,
            text=True,
        )
        raise
    tested = False
    if test_cmd:
        result = subprocess.run(
            test_cmd, cwd=str(path), capture_output=True, text=True
        )
        tested = True
        if result.returncode != 0:
            detail = (result.stderr or result.stdout or "").strip().splitlines()
            tail = "\n".join(detail[-10:])
            raise TicketError(f"Los tests fallaron:\n{tail}")
    switched = checkout_branch != integration
    try:
        if switched:
            _git(main, "checkout", integration)
        _git(main, "merge", "--ff-only", branch)
    finally:
        if switched:
            _git(main, "checkout", checkout_branch)
    summary = {
        "path": str(path),
        "branch": branch,
        "main": str(main),
        "main_branch": integration,
        "integration_branch": integration,
        "checkout_branch": checkout_branch,
        "warning": ref["warning"],
        "tests": tested,
    }
    if delete:
        try:
            removal = remove(main, path, delete_branch=True)
        except TicketError as exc:
            # El merge ya está aplicado: se informa de la limpieza pendiente
            # en vez de fingir que el worktree desapareció.
            summary["removed"] = False
            summary["remove_error"] = str(exc)
            summary["cleanup"] = f"git worktree remove --force {path}"
        else:
            summary["removed"] = True
            summary["branch_deleted"] = removal.get("branch_deleted")
    return summary


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
