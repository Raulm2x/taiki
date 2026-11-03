"""Herramienta MCP de solo lectura para skills curadas de AutoSkills.

Expone ``skills_suggest``: detecta o recibe tecnologías, cruza el registro y
devuelve sugerencias con fuente, hash, revisión y comprobación de seguridad.
No existe ninguna tool MCP de instalación o retirada: esas acciones son del
usuario por CLI.
"""

from __future__ import annotations

import os
import sqlite3
from pathlib import Path
from typing import Any

from ticketlib import workspace
from ticketlib.skills.catalog import suggest
from ticketlib.skills.detect import detect_techs
from ticketlib.skills.registry import DEFAULT_REGISTRY_URL, load_registry

REGISTRY_ENV = "TAIKI_SKILLS_REGISTRY"


def _project_root() -> Path:
    """Raíz del proyecto apuntado por el workspace o por ``TAIKI_DB``."""
    info = workspace.resolve()
    if info.get("workspace"):
        return Path(info["workspace"]).parent
    return workspace.project_root()


def _registry_source() -> str:
    return os.environ.get(REGISTRY_ENV) or DEFAULT_REGISTRY_URL


def handle_skills_suggest(
    _conn: sqlite3.Connection,
    args: dict[str, Any],
    _session: str | None,
    _actor: str,
) -> dict[str, Any]:
    """Sugiere skills para el stack detectado o para las ``techs`` recibidas."""
    root = _project_root()
    raw_techs = args.get("techs")
    techs = (
        [str(item) for item in raw_techs]
        if isinstance(raw_techs, list) and raw_techs
        else detect_techs(root)
    )
    raw_limit = args.get("limit")
    limit = 20 if raw_limit is None else int(raw_limit)
    registry = load_registry(_registry_source(), root=root)
    rows = suggest(techs, registry, limit=limit)
    return {
        "root": str(root),
        "techs": techs,
        "count": len(rows),
        "suggestions": rows,
    }


HANDLERS = {"skills_suggest": handle_skills_suggest}

__all__ = ["HANDLERS", "handle_skills_suggest"]
