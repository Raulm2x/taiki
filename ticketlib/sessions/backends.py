"""Descubrimiento de sesiones en OpenCode y Antigravity (solo lectura)."""

from __future__ import annotations

import os
import sqlite3
from pathlib import Path
from typing import Any, Iterable

OPENCODE_DB_ENV = "OPENCODE_DB"
ANTIGRAVITY_APP_DATA_DIR_ENV = "ANTIGRAVITY_APP_DATA_DIR"


def opencode_db_path() -> Path:
    """Ruta de la base de datos de OpenCode (``OPENCODE_DB`` la sobrescribe)."""
    override = os.environ.get(OPENCODE_DB_ENV)
    if override:
        return Path(override).expanduser()
    base = os.environ.get("XDG_DATA_HOME") or str(Path.home() / ".local" / "share")
    return Path(base) / "opencode" / "opencode.db"


def antigravity_brain_path() -> Path:
    """Ruta del directorio brain de Antigravity (``ANTIGRAVITY_APP_DATA_DIR`` la sobrescribe)."""
    override = os.environ.get(ANTIGRAVITY_APP_DATA_DIR_ENV)
    base = Path(override).expanduser() if override else (Path.home() / ".gemini" / "antigravity-cli")
    return base / "brain"


def _antigravity_session_info(session_id: str) -> dict[str, Any] | None:
    brain_dir = antigravity_brain_path() / session_id
    if not brain_dir.is_dir():
        return None
    title = None
    transcript = brain_dir / ".system_generated" / "logs" / "transcript.jsonl"
    if transcript.is_file():
        try:
            with transcript.open("r", encoding="utf-8", errors="replace") as f:
                line = f.readline()
                if line:
                    import json

                    data = json.loads(line)
                    content = data.get("content", "")
                    if "<USER_REQUEST>" in content:
                        req = (
                            content.split("<USER_REQUEST>")[1]
                            .split("</USER_REQUEST>")[0]
                            .strip()
                        )
                        title = req.splitlines()[0][:60] if req else None
                    if not title and content:
                        title = content.strip().splitlines()[0][:60]
        except Exception:
            pass
    return {
        "directory": None,
        "title": title or "Sesión Antigravity",
        "backend": "antigravity",
    }


def _connect_ro() -> sqlite3.Connection | None:
    path = opencode_db_path()
    if not path.is_file():
        return None
    try:
        conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=2)
        conn.row_factory = sqlite3.Row
        return conn
    except sqlite3.Error:
        return None


def _chunks(items: list[str], size: int = 400) -> Iterable[list[str]]:
    for start in range(0, len(items), size):
        yield items[start : start + size]


def session_info(session_ids: Iterable[str]) -> dict[str, dict[str, Any]]:
    """Devuelve ``{id: {directory, title, backend}}`` solo para las sesiones existentes."""
    wanted = [sid for sid in dict.fromkeys(session_ids) if sid]
    if not wanted:
        return {}
    found: dict[str, dict[str, Any]] = {}
    conn = _connect_ro()
    if conn is not None:
        try:
            for chunk in _chunks(wanted):
                placeholders = ", ".join("?" for _ in chunk)
                rows = conn.execute(
                    "SELECT id, directory, title FROM session_v2 "
                    f"WHERE id IN ({placeholders})",
                    chunk,
                )
                for row in rows:
                    found[row["id"]] = {
                        "directory": row["directory"],
                        "title": row["title"],
                        "backend": "opencode",
                    }
        except sqlite3.Error:
            pass
        finally:
            conn.close()

    for sid in wanted:
        if sid not in found:
            ag_info = _antigravity_session_info(sid)
            if ag_info is not None:
                found[sid] = ag_info

    return found


def session_parent(session_id: str) -> str | None:
    """Sesión padre (subagentes) según la base de OpenCode."""
    conn = _connect_ro()
    if conn is None:
        return None
    try:
        row = conn.execute(
            "SELECT parent_id FROM session_v2 WHERE id = ?", (session_id,)
        ).fetchone()
        return row["parent_id"] or None if row else None
    except sqlite3.Error:
        return None
    finally:
        conn.close()


def existing_sessions(session_ids: Iterable[str]) -> set[str]:
    """Conjunto de sesiones que aún existen (OpenCode o Antigravity)."""
    return set(session_info(session_ids))
