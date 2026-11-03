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
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any

from . import db
from .domain import (
    DEFAULT_ISOLATION,
    ISOLATION_MODES,
    TicketError,
    ValidationError,
    clean_branch,
)
from .paths import (
    CONFIG_NAME,
    DB_NAME,
    GITIGNORE_NAME,
    LOCAL_NAME,
    MARKER,
    MISSING,
    backups_dir,
    checkout_root,
    data_home,
    find_workspace,
    label,
    main_worktree,
    project_root,
    registry_path,
    resolve,
    resolve_db,
)

GITIGNORE = """# Base de datos y estado local de Taiki
taiki.db
taiki.db-wal
taiki.db-shm
local.json
backups/
"""


def default_config(root: Path, name: str | None = None) -> dict[str, Any]:
    return {
        "version": 1,
        "project": root.name,
        "name": name or root.name,
        "guard": False,
        "guard_auto": "scratch",
        "defaults": {"kind": "otro", "priority": "media"},
        "isolation": {"default": DEFAULT_ISOLATION, "live_branch": "main"},
        # Raíces del modo scope. Los defaults viven en config.json (versionado);
        # las rutas absolutas de la máquina van en local.json (ignorado).
        "scope": {"roots": ["."]},
        "ui": {"scope": "atencion", "refresh": 2.0},
    }


def isolation_settings(config: dict[str, Any] | None = None) -> dict[str, str]:
    """Devuelve ``{default, live_branch}`` validados con defaults seguros."""
    data = config if config is not None else load_config()
    raw = data.get("isolation") if isinstance(data, dict) else None
    if raw is None:
        raw = {}
    if not isinstance(raw, dict):
        raise ValidationError("El bloque 'isolation' debe ser un objeto")
    default = raw.get("default", DEFAULT_ISOLATION)
    if default not in ISOLATION_MODES:
        raise ValidationError(
            f"isolation.default inválido: {default!r} "
            f"(esperado uno de {', '.join(ISOLATION_MODES)})"
        )
    return {
        "default": str(default),
        "live_branch": clean_branch(raw.get("live_branch")),
    }


def scope_roots(
    config: dict[str, Any] | None = None,
    local: dict[str, Any] | None = None,
) -> list[str]:
    """Raíces del modo scope: config.json + local.json, sin duplicados.

    Las rutas relativas se resuelven contra el proyecto; ``local.json`` puede
    aportar rutas absolutas de la máquina sin versionarlas en config.json.
    """
    data = config if config is not None else load_config()
    local_data = local if local is not None else load_local()
    roots: list[str] = []
    for source, block_name in ((data, "config.json"), (local_data, "local.json")):
        if not isinstance(source, dict):
            continue
        block = source.get("scope")
        if block is None:
            continue
        if not isinstance(block, dict):
            raise ValidationError(f"scope en {block_name} debe ser un objeto")
        values = block.get("roots")
        if values is None:
            continue
        if isinstance(values, str):
            values = [values]
        if not isinstance(values, (list, tuple)):
            raise ValidationError(
                f"scope.roots en {block_name} debe ser una lista"
            )
        for value in values:
            if not isinstance(value, str) or not value.strip():
                raise ValidationError(
                    f"scope.roots en {block_name} contiene una raíz inválida"
                )
            root = value.strip()
            if root not in roots:
                roots.append(root)
    return roots or ["."]


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
