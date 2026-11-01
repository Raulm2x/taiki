"""Resolución de rutas del proyecto y del workspace.

Este módulo no importa la capa de datos: ``db`` puede usarlo sin crear el
ciclo ``db -> workspace -> db``.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path
from typing import Any

from .domain import TicketError
from .util import find_git_root

MARKER = ".taiki"
DB_NAME = "taiki.db"
CONFIG_NAME = "config.json"
LOCAL_NAME = "local.json"
GITIGNORE_NAME = ".gitignore"

MISSING = (
    "No hay una base de datos Taiki en este proyecto.\n"
    "Crea una con: taiki init --workspace"
)


def data_home() -> Path:
    """Directorio de estado de Taiki (respeta ``XDG_DATA_HOME``)."""
    base = os.environ.get("XDG_DATA_HOME") or str(Path.home() / ".local" / "share")
    return Path(base).expanduser() / "taiki"


def registry_path() -> Path:
    """Registro global de instancias MCP, compartido por todos los proyectos.

    No es una base de datos de tickets: solo guarda qué procesos MCP están
    vivos, con qué proyecto y con qué versión del código.
    """
    return data_home() / "registry.db"


def backups_dir() -> Path:
    return data_home() / "backups"


def _base(start: str | Path | None = None) -> Path:
    base = Path(start or Path.cwd()).expanduser()
    try:
        base = base.resolve()
    except OSError:
        pass
    if base.is_file():
        base = base.parent
    return base


def _git_output(root: Path, *args: str) -> str | None:
    try:
        result = subprocess.run(
            ["git", "-C", str(root), *args],
            capture_output=True,
            text=True,
        )
    except OSError:
        return None
    if result.returncode != 0:
        return None
    return result.stdout.strip()


def main_worktree(start: str | Path | None = None) -> Path | None:
    """Checkout principal del repositorio, también desde un worktree ligado."""
    base = _base(start)
    root = find_git_root(base)
    if root is None:
        return None
    common = _git_output(
        base, "rev-parse", "--path-format=absolute", "--git-common-dir"
    )
    if common:
        path = Path(common)
        return path.parent if path.name == ".git" else path
    common = _git_output(base, "rev-parse", "--git-common-dir")
    if not common:
        return root
    path = Path(common)
    if not path.is_absolute():
        path = (base / path).resolve()
    return path.parent if path.name == ".git" else root


def find_workspace(start: str | Path | None = None) -> Path | None:
    """Primer ``.taiki/`` subiendo desde ``start``, sin llegar a ``$HOME``.

    Si ``start`` está en un worktree fuera del repositorio, se usa el ``.taiki``
    del checkout principal: la base de datos es compartida.
    """
    base = _base(start)
    main = main_worktree(base)
    root = find_git_root(base)
    # Un worktree ligado puede contener un `.taiki/config.json` versionado.
    # Ese marcador no debe crear una DB local: la DB vive en el checkout
    # principal para que todos los agentes compartan tickets.
    if main is not None and root is not None and root.resolve() != main.resolve():
        marker = main / MARKER
        if marker.is_dir():
            return marker
    home = Path.home().resolve()
    stop = {home, Path(home.anchor)}
    for candidate in (base, *base.parents):
        if candidate in stop:
            break
        marker = candidate / MARKER
        if marker.is_dir():
            return marker
    if main is not None:
        marker = main / MARKER
        if marker.is_dir():
            return marker
    return None


def project_root(start: str | Path | None = None) -> Path:
    """Raíz del checkout principal, o el directorio actual sin repositorio."""
    base = _base(start)
    return main_worktree(base) or find_git_root(base) or base


def checkout_root(start: str | Path | None = None) -> Path:
    """Raíz del checkout actual (el worktree si estamos dentro de uno)."""
    base = _base(start)
    return find_git_root(base) or base


def resolve_db(start: str | Path | None = None) -> Path:
    """Ruta de la base de datos aplicable al directorio ``start``."""
    info = resolve(start)
    if info["db"] is None:
        raise TicketError(MISSING)
    return info["db"]


def resolve(start: str | Path | None = None) -> dict[str, Any]:
    """Información completa del workspace: DB, proyecto y origen."""
    workspace = find_workspace(start)
    for name in ("TAIKI_DB", "TICKETS_DB"):
        override = os.environ.get(name)
        if override:
            db_file: Path | None = Path(override).expanduser()
            source = "override"
            break
    else:
        if workspace is not None:
            db_file = workspace / DB_NAME
            source = "project"
        else:
            db_file = None
            source = "missing"
    return {
        "db": db_file,
        "workspace": workspace,
        "project": workspace.parent.name if workspace else None,
        "source": source,
        "exists": bool(db_file and db_file.exists()),
    }


def label(start: str | Path | None = None) -> str:
    """Etiqueta corta para la cabecera de la TUI."""
    info = resolve(start)
    if info["workspace"] is not None:
        return info["workspace"].parent.name
    if info["source"] == "override":
        return info["db"].parent.name
    return "sin proyecto"


__all__ = [
    "CONFIG_NAME",
    "DB_NAME",
    "GITIGNORE_NAME",
    "LOCAL_NAME",
    "MARKER",
    "MISSING",
    "backups_dir",
    "checkout_root",
    "data_home",
    "find_workspace",
    "label",
    "main_worktree",
    "project_root",
    "registry_path",
    "resolve",
    "resolve_db",
]
