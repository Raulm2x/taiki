"""Política del guard: qué es mutante, rutas y mensajes."""

from __future__ import annotations

import json
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

    # ── Modos de aislamiento: none, scope y worktree ─────────────
    conn.execute(
        "UPDATE tickets SET status = 'cancelado' "
        "WHERE code IN (?, ?) AND status = 'en_progreso'",
        (first["ticket"], pending["code"]),
    )
    conn.commit()
    base = Path(h.tmp)
    (base / "src").mkdir(exist_ok=True)
    (base / "docs").mkdir(exist_ok=True)

    none_ticket = repo.create_ticket(
        conn,
        title="Árbol vivo serial",
        isolation="none",
        created_by="opencode",
        actor_type="agente",
        session_id="ses_none",
    )
    repo.claim_ticket(
        conn, none_ticket["code"], agent="opencode", session_id="ses_none"
    )
    none_ok = guard.evaluate(
        tool="write",
        session="ses_none",
        agent="opencode",
        path=str(base / "libre.txt"),
        cwd=str(base),
    )
    h.ok(
        none_ok["allow"] and none_ok["action"] == "active",
        "none permite escribir sin worktree con ticket activo",
    )
    rival_none = repo.create_ticket(
        conn,
        title="Otra sesión serial",
        isolation="none",
        created_by="opencode",
        actor_type="agente",
        session_id="ses_none_rival",
    )
    repo.claim_ticket(
        conn,
        rival_none["code"],
        agent="opencode",
        session_id="ses_none_rival",
    )
    serial = guard.evaluate(
        tool="write",
        session="ses_none",
        agent="opencode",
        path=str(base / "libre2.txt"),
        cwd=str(base),
    )
    h.ok(
        serial["action"] == "serial"
        and not serial["allow"]
        and rival_none["code"] in serial["message"],
        "none serializa si hay otra sesión activa",
    )
    for code in (rival_none["code"], none_ticket["code"]):
        repo.transition(
            conn,
            code,
            "cancelado",
            note="fin del escenario none",
            actor="usuario",
            actor_type="usuario",
        )

    scope_ticket = repo.create_ticket(
        conn,
        title="Scope acotado",
        isolation="scope",
        paths=["src/"],
        created_by="opencode",
        actor_type="agente",
        session_id="ses_scope",
    )
    repo.claim_ticket(
        conn, scope_ticket["code"], agent="opencode", session_id="ses_scope"
    )
    inside = guard.evaluate(
        tool="write",
        session="ses_scope",
        agent="opencode",
        path=str(base / "src/a.py"),
        cwd=str(base),
    )
    h.ok(
        inside["allow"] and inside["action"] == "active",
        "scope permite un path dentro del ticket",
    )
    outside = guard.evaluate(
        tool="write",
        session="ses_scope",
        agent="opencode",
        path=str(base / "docs/a.py"),
        cwd=str(base),
    )
    h.ok(
        outside["action"] == "scope"
        and not outside["allow"]
        and "taiki update" in outside["message"],
        "scope bloquea fuera y sugiere taiki update",
    )
    command_out = guard.evaluate(
        tool="bash",
        session="ses_scope",
        agent="opencode",
        command="sed -i 's/a/b/' docs/a.py",
        cwd=str(base),
    )
    h.ok(
        command_out["action"] == "scope" and not command_out["allow"],
        "scope valida el destino de un comando",
    )
    ambiguous = guard.evaluate(
        tool="bash",
        session="ses_scope",
        agent="opencode",
        command="python3 -c \"open('src/a.py', 'w')\"",
        cwd=str(base),
    )
    h.ok(
        ambiguous["action"] == "scope" and not ambiguous["allow"],
        "scope bloquea comandos ambiguos",
    )
    rival_scope = repo.create_ticket(
        conn,
        title="Scope solapado",
        isolation="scope",
        paths=["src/lib/"],
        created_by="opencode",
        actor_type="agente",
        session_id="ses_scope_rival",
    )
    repo.claim_ticket(
        conn,
        rival_scope["code"],
        agent="opencode",
        session_id="ses_scope_rival",
    )
    overlap = guard.evaluate(
        tool="write",
        session="ses_scope",
        agent="opencode",
        path=str(base / "src/lib/x.py"),
        cwd=str(base),
    )
    h.ok(
        overlap["action"] == "scope-overlap"
        and not overlap["allow"]
        and rival_scope["code"] in overlap["message"],
        "scope bloquea solape de paths con otro activo",
    )
    for code in (rival_scope["code"], scope_ticket["code"]):
        repo.transition(
            conn,
            code,
            "cancelado",
            note="fin del escenario scope",
            actor="usuario",
            actor_type="usuario",
        )

    res_ticket = repo.create_ticket(
        conn,
        title="Recurso reservado",
        isolation="scope",
        paths=["docs/"],
        resources=["package:jq"],
        created_by="opencode",
        actor_type="agente",
        session_id="ses_res",
    )
    repo.claim_ticket(
        conn, res_ticket["code"], agent="opencode", session_id="ses_res"
    )
    res_rival = repo.create_ticket(
        conn,
        title="Recurso solapado",
        isolation="scope",
        paths=["otro/"],
        resources=["package:jq"],
        created_by="opencode",
        actor_type="agente",
        session_id="ses_res_rival",
    )
    repo.claim_ticket(
        conn, res_rival["code"], agent="opencode", session_id="ses_res_rival"
    )
    res_overlap = guard.evaluate(
        tool="bash",
        session="ses_res",
        agent="opencode",
        command="pacman -S jq",
        cwd=str(base),
    )
    h.ok(
        res_overlap["action"] == "scope-overlap"
        and not res_overlap["allow"],
        "scope bloquea solape de resources con otro activo",
    )
    for code in (res_rival["code"], res_ticket["code"]):
        repo.transition(
            conn,
            code,
            "cancelado",
            note="fin del escenario de recursos",
            actor="usuario",
            actor_type="usuario",
        )

    wt_ticket = repo.create_ticket(
        conn,
        title="Worktree clásico",
        isolation="worktree",
        paths=["src/"],
        created_by="opencode",
        actor_type="agente",
        session_id="ses_wt",
    )
    repo.claim_ticket(
        conn, wt_ticket["code"], agent="opencode", session_id="ses_wt"
    )
    wt_rival = repo.create_ticket(
        conn,
        title="Otra sesión worktree",
        isolation="worktree",
        created_by="opencode",
        actor_type="agente",
        session_id="ses_wt_rival",
    )
    repo.claim_ticket(
        conn, wt_rival["code"], agent="opencode", session_id="ses_wt_rival"
    )
    wt_block = guard.evaluate(
        tool="write",
        session="ses_wt",
        agent="opencode",
        path=str(base / "src/a.py"),
        cwd=str(base),
    )
    h.ok(
        wt_block["action"] == "worktree" and not wt_block["allow"],
        "worktree sigue exigiendo worktree con otra sesión activa",
    )
    repo.transition(
        conn,
        wt_rival["code"],
        "cancelado",
        note="fin del escenario worktree",
        actor="usuario",
        actor_type="usuario",
    )
    wt_free = guard.evaluate(
        tool="write",
        session="ses_wt",
        agent="opencode",
        path=str(base / "docs/fuera.py"),
        cwd=str(base),
    )
    h.ok(
        wt_free["allow"] and wt_free["action"] == "active",
        "worktree no valida paths en la escritura",
    )
    repo.transition(
        conn,
        wt_ticket["code"],
        "cancelado",
        note="fin del escenario worktree",
        actor="usuario",
        actor_type="usuario",
    )

    ws = base / "proj"
    (ws / ".taiki").mkdir(parents=True)
    (ws / "src").mkdir()
    extra = base / "etc-simulado"
    extra.mkdir()
    (ws / ".taiki" / "config.json").write_text(
        json.dumps({"guard": True, "scope": {"roots": [".", str(extra)]}}),
        encoding="utf-8",
    )
    root_ticket = repo.create_ticket(
        conn,
        title="Raíz configurada",
        isolation="scope",
        paths=["src/"],
        created_by="opencode",
        actor_type="agente",
        session_id="ses_root",
    )
    repo.claim_ticket(
        conn, root_ticket["code"], agent="opencode", session_id="ses_root"
    )
    in_project = guard.evaluate(
        tool="write",
        session="ses_root",
        agent="opencode",
        path=str(ws / "src/a.py"),
        cwd=str(ws),
    )
    h.ok(
        in_project["allow"] and in_project["action"] == "active",
        "la raíz '.' cubre el proyecto configurado",
    )
    in_extra = guard.evaluate(
        tool="write",
        session="ses_root",
        agent="opencode",
        path=str(extra / "nota.txt"),
        cwd=str(ws),
    )
    h.ok(
        in_extra["action"] == "scope" and not in_extra["allow"],
        "una raíz configurada no es outside y aun así valida scope",
    )
    repo.transition(
        conn,
        root_ticket["code"],
        "cancelado",
        note="fin del escenario de raíces",
        actor="usuario",
        actor_type="usuario",
    )

    scale_messages = [
        guard_policy._serial_message("write", [{"code": "T-0001"}]),
        guard_policy._scope_message("la ruta '/x'", "T-0002"),
        guard_policy._overlap_message(
            "T-0002", {"code": "T-0003", "paths": ["src/ ~ src/"], "resources": []}
        ),
    ]
    h.ok(
        all(len(message) < 180 for message in scale_messages)
        and any("taiki update" in message for message in scale_messages),
        "los mensajes de modo/scope son breves y accionables",
    )
