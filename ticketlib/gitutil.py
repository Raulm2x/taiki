"""Utilidades de Git que no dependen del almacén de tickets."""

from __future__ import annotations

import subprocess
from pathlib import Path


def resolve_commit(ref: str, cwd: str | Path | None = None) -> str | None:
    """Hash completo de un commit (acepta cortos), o ``None`` si no existe."""
    value = (ref or "").strip()
    if not value:
        return None
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--verify", f"{value}^{{commit}}"],
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if result.returncode != 0:
        return None
    return result.stdout.strip() or None
