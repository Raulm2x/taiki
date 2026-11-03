"""Comandos ``taiki sys``: workspace de sistema, raíces y snapshots.

El workspace de sistema vive en un repositorio Git propio (por defecto
``~/.local/share/taiki-sys``) con su ``.taiki/`` y su DB local. Los ficheros
vivos del sistema siguen siendo la fuente de verdad; el repo de sistema actúa
como ledger de snapshots y exportaciones.
"""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any

from ticketlib import commits, render, workspace
from ticketlib.domain import TicketError
from ticketlib.paths import MARKER


def project_root() -> Path:
    """Raíz del workspace de sistema, también con ``TAIKI_DB`` explícito."""
    info = workspace.resolve()
    db_file = info.get("db")
    if info.get("source") == "override" and db_file is not None:
        candidate = Path(db_file).expanduser()
        if candidate.parent.name == MARKER:
            return candidate.parent.parent
    folder = info.get("workspace")
    if folder is not None:
        return Path(folder).parent
    return workspace.project_root()


def _absolute(value: str, project: Path) -> Path:
    path = Path(value).expanduser()
    return path if path.is_absolute() else project / path


def _git_state(project: Path) -> dict[str, Any]:
    """Repo Git del workspace y cambios sin commitear (si no hay, vacío)."""
    try:
        root = commits.toplevel(project)
    except TicketError:
        return {"root": None, "branch": None, "dirty": 0, "clean": True}
    dirty = len(commits.unstaged(root))
    return {
        "root": str(root),
        "branch": commits.current_branch(root),
        "dirty": dirty,
        "clean": dirty == 0,
    }


def status(root: str | Path | None = None) -> dict[str, Any]:
    """Estado del workspace de sistema: repo, raíces, aislamiento y dotfiles."""
    project = Path(root).expanduser() if root is not None else project_root()
    folder = project / MARKER
    config = workspace.load_config(folder)
    local = workspace.load_local(folder)
    info = workspace.resolve(project)
    roots = [str(_absolute(item, project)) for item in workspace.scope_roots(config, local)]
    return {
        "workspace": {
            "project": str(project),
            "taiki": str(folder),
            "db": str(info["db"]) if info.get("db") else None,
            "source": info.get("source"),
        },
        "git": _git_state(project),
        "scope": {"roots": roots},
        "isolation": workspace.isolation_settings(config),
        "verify": {"command": workspace.verify_command(config)},
        "dotfiles": workspace.dotfiles_settings(config, local),
    }


def cmd_sys_status(args: argparse.Namespace) -> int:
    report = status(getattr(args, "root", None))
    if getattr(args, "json", False):
        print(render.to_json(report))
        return 0
    ws = report["workspace"]
    print(f"Workspace: {ws['project']} ({ws['source']})")
    print(f"Taiki: {ws['taiki']}")
    print(f"Base de datos: {ws['db'] or 'sin DB'}")
    git = report["git"]
    print(
        "Repo git: "
        f"{git['root'] or 'sin repositorio'} "
        f"({git['branch'] or 'sin rama'}, {git['dirty']} cambio(s))"
    )
    print("Raíces: " + (", ".join(report["scope"]["roots"]) or "ninguna"))
    isolation = report["isolation"]
    print(
        f"Aislamiento: {isolation['default']} · rama live {isolation['live_branch']}"
    )
    print(f"Verify: {report['verify']['command'] or 'sin comando'}")
    dotfiles = report["dotfiles"]
    if dotfiles["detected"]:
        print(f"Dotfiles: {dotfiles['detected']} ({dotfiles['source']})")
    elif dotfiles["repo"]:
        print(f"Dotfiles: {dotfiles['repo']} (sin .git)")
    else:
        print(f"Dotfiles: no detectado (live {dotfiles['live']})")
    return 0
