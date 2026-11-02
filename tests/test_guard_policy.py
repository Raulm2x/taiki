"""Política del guard: qué es mutante, rutas y mensajes."""

from __future__ import annotations

import os
from pathlib import Path

from ticketlib import commits, guard, repo, scratch
from ticketlib.guard import policy as guard_policy
from tests.seeds import git_repo
from tests.support import Harness


def run(h: Harness) -> None:
    conn = h.conn
    # ── Guard: ningún cambio sin ticket ──────────────────────────
    os.environ.pop("TICKETS_GUARD", None)
    h.ok(not guard.is_mutating("read"), "el guard deja pasar las lecturas")
    h.ok(
        guard.is_mutating("write")
        and guard.is_mutating("edit")
        and guard.is_mutating(
            "bash", command="rm -rf /home/rowlm/.config/tmp"
        ),
        "el guard vigila escrituras y comandos mutantes",
    )
    h.ok(
        not guard.command_is_mutating("git status")
        and not guard.command_is_mutating("rg foo ~/.config")
        and not guard.command_is_mutating("git commit -m x")
        and not guard.command_is_mutating("git push origin main")
        and guard.command_is_mutating("echo hola > nota.txt")
        and guard.command_is_mutating("sed -i 's/a/b/' f"),
        "la heurística distingue leer de escribir y deja pasar git",
    )
    h.ok(
        not guard.command_touches_home(
            "cd ~/.config && rm -rf /tmp/opencode/x",
            Path.home() / ".config",
        )
        and guard.command_touches_home(
            "rm -rf build", Path.home() / ".config"
        )
        and guard.command_touches_home("rm -rf /home/rowlm/x"),
        "touches_home no confunde ~/.config con una ruta absoluta",
    )
    h.ok(
        not guard.path_guarded("/tmp/opencode/x")
        and not guard.path_guarded("/usr/share/noctalia/x")
        and guard.path_guarded(Path.home() / ".config/AGENTS.md"),
        "el guard ignora rutas fuera del HOME y efímeras",
    )

    main_repo = git_repo(Path(h.tmp) / "main_repo")
    denied = guard.evaluate(
        tool="bash",
        session="ses_branch",
        agent="worker",
        command="git commit -m x",
        cwd=str(main_repo),
    )
    h.ok(
        guard_policy.protected_git("git push origin main", "agent/T-0001-x")
        == "git push"
        and guard_policy.protected_git("git merge develop", "main") == "git merge"
        and guard_policy.protected_git("git commit -m x", "master") == "git commit"
        and guard_policy.protected_git("git commit -m x", "agent/T-0001-x") is None
        and not commits.branch_policy("agent/T-0001-x", "T-0002")["ok"]
        and commits.branch_policy("agent/T-0001-x", "T-0001")["ok"]
        and commits.branch_policy("hotfix/T-0001", "T-0001")["ok"]
        and not denied["allow"]
        and denied["action"] == "protected-branch",
        "el guard protege main y exige la rama del ticket",
    )

    first = guard.evaluate(
        tool="write",
        session="ses_guard_a",
        agent="opencode",
        path=str(Path.home() / ".config/AGENTS.md"),
    )
    h.ok(
        not first["allow"]
        and first["action"] == "created"
        and bool(first["ticket"]),
        "una escritura sin ticket crea uno y bloquea",
    )
    created_guard = repo.get_ticket(conn, first["ticket"])
    h.ok(
        created_guard["status"] == "en_progreso"
        and created_guard["assigned_to"] == "opencode"
        and scratch.is_scratch(created_guard),
        "sin épica el ticket automático es un scratch oculto y reclamado",
    )
    h.ok(
        created_guard["code"]
        not in {row["code"] for row in repo.list_tickets(conn, limit=None)},
        "el ticket automático no ensucia la tabla",
    )
    second = guard.evaluate(
        tool="write",
        session="ses_guard_a",
        agent="opencode",
        path=str(Path.home() / ".config/AGENTS.md"),
    )
    h.ok(
        second["allow"] and second["action"] == "active",
        "el segundo intento ya pasa",
    )

    pending = repo.create_ticket(
        conn,
        title="Pendiente de la sesión",
        created_by="opencode",
        actor_type="agente",
        assigned_to="opencode",
        session_id="ses_guard_b",
    )
    reused = guard.evaluate(
        tool="edit",
        session="ses_guard_b",
        agent="opencode",
        path=str(Path.home() / ".config/x"),
    )
    h.ok(
        not reused["allow"]
        and reused["action"] == "claimed"
        and reused["ticket"] == pending["code"],
        "el guard reutiliza el ticket pendiente de la sesión",
    )


    # ── Mensajes del guard compactos y con nombres de herramienta ───────
    messages = [
        guard_policy._message("created", "S-0001", "titulo", None, True),
        guard_policy._message(
            "created",
            "T-0099",
            "titulo",
            {"code": "T-0016", "title": "Epica"},
            False,
        ),
        guard_policy._message("claimed", "T-0099", "titulo"),
        guard_policy._message("blocked", "", ""),
        guard_policy._parallel_message(
            "git add -A", [{"code": "T-0001"}]
        ),
        guard_policy._worktree_message(
            {"code": "T-0002"}, [{"code": "T-0001"}]
        ),
    ]
    h.ok(
        all(len(message) < 180 for message in messages),
        "los mensajes del guard ocupan menos de 180 caracteres",
    )
    h.ok(
        not any("taiki_" in message for message in messages),
        "los mensajes usan los nombres reales de las herramientas",
    )
