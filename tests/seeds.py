"""Datos de partida reutilizables por los módulos de prueba."""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any

from ticketlib import githook, repo

import cli


def seed_basic(conn: Any) -> tuple[dict[str, Any], dict[str, Any]]:
    """Dos tickets estables: T-0001 en progreso y T-0002 nuevo con enlace."""
    first = repo.create_ticket(
        conn,
        title="Arreglar arranque de Noctalia",
        body="El shell no recarga la configuración al guardar.",
        kind="bug",
        priority="alta",
        project=".config",
        path=str(Path.home() / ".config"),
        tags=["noctalia", "shell"],
        created_by="agente:test",
    )
    repo.claim_ticket(conn, first["code"], agent="agente:test")
    second = repo.create_ticket(
        conn,
        title="Añadir exportación de respaldos",
        body="Exportar la base a JSON de forma periódica.",
        kind="funcionalidad",
        priority="media",
        project=".config",
        tags=["backup"],
    )
    repo.comment_ticket(conn, second["code"], "Usar sqlite3 .backup además del JSON.")
    repo.link_ticket(conn, second["code"], "url", "https://sqlite.org/backup.html")
    return first, second


def completed(conn: Any, title: str) -> dict[str, Any]:
    """Ticket cerrado con resolución, listo para confirmar."""
    ticket = repo.create_ticket(conn, title=title, project=".config")
    repo.claim_ticket(conn, ticket["code"], agent="opencode")
    return repo.complete_ticket(conn, ticket["code"], resolution="Listo")


def seed_tui(conn: Any) -> dict[str, Any]:
    """Población mínima para la lista, el detalle y el panel de actividad."""
    seed_basic(conn)
    live = repo.create_ticket(conn, title="Sesión viva", project=".config")
    repo.claim_ticket(conn, live["code"], agent="opencode")
    repo.comment_ticket(conn, live["code"], "nota", session_id="ses_viva1")
    pending = repo.create_ticket(conn, title="Sin completar", project=".config")
    first = completed(conn, "Uno completado")
    second = completed(conn, "Dos completado")
    repo.link_ticket(conn, first["code"], "sesion", "ses_viva1")
    return {
        "live": live,
        "pending": pending,
        "completed": [first, second],
        "session": "ses_viva1",
    }


def git_repo(path: Path) -> Path:
    """Inicializa un repositorio Git con identidad mínima."""
    path.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", "-q", str(path)], check=True)
    subprocess.run(
        ["git", "-C", str(path), "config", "user.email", "t@t"], check=True
    )
    subprocess.run(["git", "-C", str(path), "config", "user.name", "t"], check=True)
    return path


def repo_with_hooks(path: Path) -> Path:
    """Repositorio Git con los cuatro hooks de tickets instalados."""
    git_repo(path)
    cli.install_post_commit_hook(path)
    githook.install_commit_msg_hook(path)
    githook.install_pre_push_hook(path)
    githook.install_pre_commit_hook(path)
    return path
