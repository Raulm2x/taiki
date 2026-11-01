"""Identidad del proceso MCP: qué código cargó y cuándo arrancó.

El servidor MCP es un proceso de larga vida: si se actualiza el código de
tickets, sigue ejecutando la versión anterior hasta que OpenCode lo reinicia.
Este módulo guarda en ``meta`` una huella del código con el que arrancó para
que el CLI y la TUI puedan avisar antes de que cree tickets mal numerados.
"""

from __future__ import annotations

import hashlib
import os
import sqlite3
from pathlib import Path
from typing import Any

from . import db
from .util import now

ROOT = Path(__file__).resolve().parent.parent

# Módulos que el servidor MCP no importa (solo CLI/TUI). Se excluyen para no
# dar falsos avisos de "MCP desactualizado" cuando cambian.
EXCLUDED = (
    "ticketlib/audit.py",
    "ticketlib/guard",
    "ticketlib/githook.py",
    "ticketlib/deeplink.py",
)


def _excluded(relative: str) -> bool:
    return any(
        relative == item or relative.startswith(item + "/") for item in EXCLUDED
    )


def _tracked_files() -> list[Path]:
    files = [ROOT / "server.py"]
    for folder in ("mcp_layer", "ticketlib"):
        for path in sorted((ROOT / folder).rglob("*.py")):
            relative = path.relative_to(ROOT).as_posix()
            if "__pycache__" in path.parts or _excluded(relative):
                continue
            files.append(path)
    return files


def _compute() -> str:
    digest = hashlib.sha256()
    for path in _tracked_files():
        digest.update(path.relative_to(ROOT).as_posix().encode())
        digest.update(b"\0")
        digest.update(path.read_bytes())
    return digest.hexdigest()[:12]


# Huella del código que este proceso cargó al importar el módulo.
LOADED = _compute()


def code_fingerprint() -> str:
    """Huella del código cargado por este proceso (no la del disco)."""
    return LOADED


def disk_fingerprint() -> str:
    """Huella actual del código en disco, sin caché."""
    return _compute()


def is_stale() -> bool:
    """¿El proceso lleva código distinto al que hay ahora en disco?"""
    return LOADED != disk_fingerprint()


def register(conn: sqlite3.Connection, version: str = "") -> dict[str, Any]:
    """Registra el arranque del MCP en ``meta`` (idempotente)."""
    fingerprint = code_fingerprint()
    started = now()
    db.meta_set(conn, "mcp_fingerprint", fingerprint)
    db.meta_set(conn, "mcp_pid", str(os.getpid()))
    db.meta_set(conn, "mcp_started", started)
    if version:
        db.meta_set(conn, "mcp_version", version)
    return {
        "fingerprint": fingerprint,
        "pid": os.getpid(),
        "started": started,
        "version": version,
    }


def status(conn: sqlite3.Connection) -> dict[str, Any]:
    """Compara el código en disco con el que cargó el último MCP."""
    stored = db.meta_get(conn, "mcp_fingerprint")
    current = disk_fingerprint()
    pid = db.meta_get(conn, "mcp_pid")
    started = db.meta_get(conn, "mcp_started")
    return {
        "stored": stored,
        "current": current,
        "pid": int(pid) if pid and pid.isdigit() else None,
        "started": started,
        "missing": not bool(stored),
        "stale": bool(stored and stored != current),
    }
