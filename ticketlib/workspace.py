"""Espacios de trabajo: una base de datos y su configuración por proyecto.

Un proyecto Taiki se reconoce por un directorio ``.taiki/`` en su raíz. Dentro
viven la base de datos (``taiki.db``), la configuración versionable
(``config.json``) y los ajustes locales (``local.json``, ignorado por Git).

La resolución de la base de datos es, por orden:

1. ``TAIKI_DB`` (o el alias histórico ``TICKETS_DB``).
2. El ``.taiki/taiki.db`` del proyecto, subiendo desde el directorio actual.

No hay base de datos global: usar Taiki es una decisión por proyecto. Si no se
encuentra un ``.taiki``, se lanza un error que indica cómo crearlo. La búsqueda
se detiene antes del directorio personal para no heredar por accidente un
``~/.taiki`` en todos los proyectos.
"""

from __future__ import annotations

import json
import os
import sqlite3
from datetime import datetime
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
    "Crea una con: ticket init --workspace"
)

GITIGNORE = """# Base de datos y estado local de Taiki
taiki.db
taiki.db-wal
taiki.db-shm
local.json
backups/
"""


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


def find_workspace(start: str | Path | None = None) -> Path | None:
    """Primer ``.taiki/`` subiendo desde ``start``, sin llegar a ``$HOME``."""
    base = _base(start)
    home = Path.home().resolve()
    stop = {home, Path(home.anchor)}
    for candidate in (base, *base.parents):
        if candidate in stop:
            break
        marker = candidate / MARKER
        if marker.is_dir():
            return marker
    return None


def project_root(start: str | Path | None = None) -> Path:
    """Raíz Git del proyecto, o el directorio actual si no hay repositorio."""
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


def default_config(root: Path, name: str | None = None) -> dict[str, Any]:
    return {
        "version": 1,
        "project": root.name,
        "name": name or root.name,
        "guard": False,
        "defaults": {"kind": "otro", "priority": "media"},
        "ui": {"scope": "atencion", "refresh": 2.0},
    }


def load_config(workspace: str | Path | None = None) -> dict[str, Any]:
    """Lee ``config.json`` del workspace indicado (o del actual)."""
    folder = Path(workspace) if workspace is not None else find_workspace()
    if folder is None:
        return {}
    path = folder / CONFIG_NAME
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def load_local(workspace: str | Path | None = None) -> dict[str, Any]:
    folder = Path(workspace) if workspace is not None else find_workspace()
    if folder is None:
        return {}
    path = folder / LOCAL_NAME
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def save_config(
    config: dict[str, Any],
    workspace: str | Path | None = None,
) -> Path:
    folder = Path(workspace) if workspace is not None else find_workspace()
    if folder is None:
        raise TicketError("No hay un workspace .taiki en este directorio")
    path = folder / CONFIG_NAME
    path.write_text(
        json.dumps(config, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    return path


def _copy_database(source: Path, target: Path) -> None:
    """Copia consistente con la API de backup de SQLite."""
    target.parent.mkdir(parents=True, exist_ok=True)
    src = sqlite3.connect(str(source))
    dst = sqlite3.connect(str(target))
    try:
        with dst:
            src.backup(dst)
    finally:
        src.close()
        dst.close()


def init_workspace(
    root: str | Path | None = None,
    *,
    adopt: str | Path | None = None,
    name: str | None = None,
    force: bool = False,
) -> dict[str, Any]:
    """Crea ``.taiki/`` en el proyecto (o adopta una base existente)."""
    from . import db  # import local: evita el ciclo workspace <-> db

    root_path = Path(root).expanduser() if root else project_root()
    if not root_path.exists():
        raise TicketError(f"No existe el directorio del proyecto: {root_path}")
    folder = root_path / MARKER
    folder.mkdir(parents=True, exist_ok=True, mode=0o700)

    config_path = folder / CONFIG_NAME
    if not config_path.exists():
        save_config(default_config(root_path, name), folder)
    gitignore = folder / GITIGNORE_NAME
    if not gitignore.exists():
        gitignore.write_text(GITIGNORE, encoding="utf-8")

    target = folder / DB_NAME
    adopted = False
    if adopt is not None:
        source = Path(adopt).expanduser()
        if not source.exists():
            raise TicketError(f"No existe la base de datos a adoptar: {source}")
        if target.exists() and not force:
            raise TicketError(
                f"Ya existe {target}; usa --force para reemplazarla (se guarda copia)"
            )
        if target.exists():
            stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
            target.replace(target.with_name(f"{target.name}.bak-{stamp}"))
        _copy_database(source, target)
        adopted = True

    conn = db.connect(target)
    conn.close()
    return {
        "workspace": folder,
        "db": target,
        "config": config_path,
        "adopted": adopted,
    }
