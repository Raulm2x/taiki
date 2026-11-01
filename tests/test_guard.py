"""Guard de escritura y tickets scratch."""

from __future__ import annotations

import contextlib
import io
import json
import os
import sys
from pathlib import Path
import cli
from ticketlib import audit as audit_lib, db, guard, repo, scratch
from tests.support import Harness


def run(h: Harness) -> None:
    conn = h.conn
    tmp = str(h.tmp)

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

    # ── Guard jerárquico: la épica abierta es el padre ────────────
    epic = repo.create_ticket(
        conn,
        title="Épica abierta de la sesión",
        project=".config",
        created_by="opencode",
        actor_type="agente",
        session_id="ses_guard_epic",
    )
    epic_task = repo.create_ticket(
        conn,
        title="Tarea de la épica",
        parent_id=epic["code"],
        created_by="opencode",
        actor_type="agente",
        session_id="ses_guard_epic",
    )
    repo.claim_ticket(conn, epic_task["code"], agent="opencode")
    repo.complete_ticket(
        conn, epic_task["code"], resolution="ok", actor="opencode"
    )
    conn.execute(
        "UPDATE tickets SET status = 'completado' WHERE id = ?",
        (epic["id"],),
    )
    conn.commit()
    nested = guard.evaluate(
        tool="write",
        session="ses_guard_epic",
        agent="opencode",
        path=str(Path.home() / ".config/x"),
    )
    h.ok(
        not nested["allow"]
        and nested["action"] == "created"
        and nested["ticket"].startswith(epic["code"] + "."),
        "el guard cuelga el ticket nuevo de la épica sin confirmar",
    )
    nested_ticket = repo.get_ticket(conn, nested["ticket"])
    h.ok(
        nested_ticket["parent_id"] == epic["id"]
        and epic["code"] in nested["message"]
        and not scratch.is_scratch(nested_ticket),
        "con épica abierta la tarea es visible y queda enlazada",
    )
    off = guard.evaluate(
        tool="write",
        session="ses_guard_auto_off",
        agent="opencode",
        path=str(Path.home() / ".config/x"),
        environ={"TICKETS_GUARD_AUTO": "off"},
    )
    h.ok(
        not off["allow"]
        and off["action"] == "blocked"
        and not off["ticket"],
        "TICKETS_GUARD_AUTO=off bloquea sin crear nada",
    )
    off_new = guard.evaluate(
        tool="write",
        session="ses_guard_auto_off_new",
        agent="opencode",
        path=str(Path.home() / ".config/x"),
        environ={"TAIKI_GUARD_AUTO": "off"},
    )
    h.ok(
        not off_new["allow"]
        and off_new["action"] == "blocked"
        and not off_new["ticket"],
        "TAIKI_GUARD_AUTO=off bloquea sin crear nada",
    )
    forced = guard.evaluate(
        tool="write",
        session="ses_guard_auto_ticket",
        agent="opencode",
        path=str(Path.home() / ".config/x"),
        environ={"TICKETS_GUARD_AUTO": "ticket"},
    )
    forced_ticket = repo.get_ticket(conn, forced["ticket"])
    h.ok(
        forced["action"] == "created"
        and not scratch.is_scratch(forced_ticket),
        "TICKETS_GUARD_AUTO=ticket fuerza un ticket visible",
    )

    confirmed = repo.create_ticket(
        conn,
        title="Épica ya confirmada",
        project=".config",
        created_by="opencode",
        actor_type="agente",
        session_id="ses_guard_confirmed",
    )
    confirmed_task = repo.create_ticket(
        conn,
        title="Tarea confirmada",
        parent_id=confirmed["code"],
        created_by="opencode",
        actor_type="agente",
        session_id="ses_guard_confirmed",
    )
    repo.claim_ticket(conn, confirmed_task["code"], agent="opencode")
    repo.complete_ticket(
        conn, confirmed_task["code"], resolution="ok", actor="opencode"
    )
    conn.execute(
        "UPDATE tickets SET status = 'completado' WHERE id = ?",
        (confirmed["id"],),
    )
    conn.commit()
    repo.confirm_tickets(conn, [confirmed["code"]])
    after_confirm = guard.evaluate(
        tool="write",
        session="ses_guard_confirmed",
        agent="opencode",
        path=str(Path.home() / ".config/x"),
    )
    h.ok(
        after_confirm["action"] == "created"
        and not after_confirm["ticket"].startswith(confirmed["code"] + "."),
        "una épica confirmada se considera cerrada",
    )

    other = repo.create_ticket(
        conn,
        title="Épica de otro proyecto",
        project="otro-proyecto",
        created_by="opencode",
        actor_type="agente",
        session_id="ses_guard_other",
    )
    other_task = repo.create_ticket(
        conn,
        title="Tarea de otro proyecto",
        parent_id=other["code"],
        created_by="opencode",
        actor_type="agente",
        session_id="ses_guard_other",
    )
    repo.claim_ticket(conn, other_task["code"], agent="opencode")
    repo.complete_ticket(
        conn, other_task["code"], resolution="ok", actor="opencode"
    )
    conn.execute(
        "UPDATE tickets SET status = 'completado' WHERE id = ?",
        (other["id"],),
    )
    conn.commit()
    other_result = guard.evaluate(
        tool="write",
        session="ses_guard_other",
        agent="opencode",
        path=str(Path.home() / ".config/x"),
    )
    h.ok(
        other_result["action"] == "created"
        and not other_result["ticket"].startswith(other["code"] + "."),
        "una épica de otro proyecto no se reutiliza",
    )

    strict = guard.evaluate(
        tool="write",
        session="ses_guard_c",
        agent="opencode",
        path=str(Path.home() / ".config/x"),
        auto=False,
    )
    h.ok(
        not strict["allow"]
        and strict["action"] == "blocked"
        and not strict["ticket"],
        "sin auto solo bloquea",
    )
    h.ok(
        guard.evaluate(
            tool="write",
            session="ses_guard_d",
            agent="opencode",
            path=str(Path.home() / ".config/x"),
            environ={"TICKETS_GUARD": "off"},
        )["action"]
        == "disabled",
        "TICKETS_GUARD=off desactiva el guard",
    )
    h.ok(
        guard.evaluate(
            tool="write",
            session="ses_guard_d2",
            agent="opencode",
            path=str(Path.home() / ".config/x"),
            environ={"TAIKI_GUARD": "off"},
        )["action"]
        == "disabled",
        "TAIKI_GUARD=off desactiva el guard",
    )
    h.ok(
        guard.evaluate(
            tool="write",
            session="ses_guard_e",
            agent="opencode",
            path="/tmp/opencode/x",
        )["allow"],
        "escribir en /tmp no exige ticket",
    )
    h.ok(
        guard.evaluate(
            tool="bash",
            session="ses_guard_f",
            agent="opencode",
            command="rm -f /tmp/opencode/x",
        )["allow"],
        "un comando de scratch no exige ticket",
    )

    h.ok(
        guard.normalize_tool("write_file") == "write"
        and guard.normalize_tool("run_shell_command") == "bash"
        and guard.normalize_tool("edit_file") == "edit",
        "el guard entiende los nombres de otros agentes",
    )
    parsed = guard.parse_hook_input(
        {
            "tool_name": "write_file",
            "tool_input": {"file_path": "/home/rowlm/.config/x"},
            "session_id": "ses_hook",
            "agent_name": "antigravity",
        }
    )
    h.ok(
        parsed["tool"] == "write_file"
        and parsed["path"] == "/home/rowlm/.config/x"
        and parsed["agent"] == "antigravity",
        "parse_hook_input extrae el evento genérico",
    )

    out = io.StringIO()
    saved_stdin = sys.stdin
    try:
        sys.stdin = io.StringIO(
            '{"tool_name": "write_file", "tool_input": '
            '{"path": "/home/rowlm/.config/hook.txt"}, '
            '"session_id": "ses_hook_a"}'
        )
        with contextlib.redirect_stdout(out):
            code = cli.main(["guard", "--hook"])
        payload = json.loads(out.getvalue())
        h.ok(
            code == 2
            and payload["decision"] == "deny"
            and payload["ticket"],
            "el modo hook bloquea y crea el ticket",
        )
        sys.stdin = io.StringIO(
            '{"tool_name": "write_file", "tool_input": '
            '{"path": "/home/rowlm/.config/hook.txt"}, '
            '"session_id": "ses_hook_a"}'
        )
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = cli.main(["guard", "--hook"])
        payload = json.loads(out.getvalue())
        h.ok(
            code == 0 and payload["decision"] == "allow",
            "el modo hook deja pasar tras crear el ticket",
        )
    finally:
        sys.stdin = saved_stdin

    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        code = cli.main(
            [
                "guard",
                "--tool",
                "write",
                "--session",
                "ses_guard_g",
                "--path",
                str(Path.home() / ".config/y"),
                "--json",
            ]
        )
    guard_payload = json.loads(out.getvalue())
    h.ok(
        code == 3
        and not guard_payload["allow"]
        and guard_payload["ticket"],
        "taiki guard devuelve 3 y JSON",
    )

    # ── Guard: publicar no exige ticket y las lecturas tampoco ───
    h.ok(
        not guard.command_is_mutating(
            "cd ~/.config && git push 2>&1 | tail -3"
        )
        and not guard.command_is_mutating(
            "git add -A && git commit -m 'x (T-0001)' && git push"
        )
        and not guard.command_is_mutating('python3 -c "print(1 > 0)"')
        and not guard.command_is_mutating("taiki list --limit 5")
        and not guard.command_is_mutating("git stash list")
        and guard.command_is_mutating("echo hola > nota.txt")
        and guard.command_is_mutating("sed -i 's/a/b/' f")
        and guard.command_is_mutating("git checkout -- .")
        and guard.command_is_mutating("env FOO=1 rm x"),
        "el guard distingue publicar de escribir, con pipes y comillas",
    )
    h.ok(
        guard.command_is_mutating(
            "python3 - <<'PY'\nopen('x', 'w').write('hola')\nPY"
        )
        and not guard.command_is_mutating(
            "python3 - <<'PY'\nprint('a > b')\nPY"
        ),
        "los heredocs solo bloquean cuando el script escribe",
    )

    temp = scratch.create_or_reuse(
        conn, agent="opencode", session_id="ses_scratch"
    )
    h.ok(
        scratch.is_scratch(temp)
        and temp["status"] == "en_progreso"
        and bool(scratch.expires_at(conn, temp["code"])),
        "scratch.create_or_reuse crea un temporal con caducidad",
    )
    again = scratch.create_or_reuse(
        conn, agent="opencode", session_id="ses_scratch"
    )
    h.ok(
        again["code"] == temp["code"],
        "el scratch de la sesión se reutiliza",
    )
    other_scratch = scratch.create_or_reuse(
        conn, agent="opencode", session_id="ses_scratch_otra"
    )
    h.ok(
        other_scratch["code"] != temp["code"],
        "otra sesión recibe su propio scratch",
    )

    topic = repo.create_ticket(
        conn,
        title="Ticket con el tema scratch",
        tags=["scratch"],
    )
    h.ok(
        topic["code"]
        in {row["code"] for row in repo.list_tickets(conn, limit=None)},
        "la etiqueta de tema scratch no oculta el ticket",
    )

    listed = {row["code"] for row in repo.list_tickets(conn, limit=None)}
    h.ok(
        temp["code"] not in listed,
        "el scratch no aparece en list_tickets",
    )
    with_scratch = {
        row["code"]
        for row in repo.list_tickets(
            conn, limit=None, include_scratch=True
        )
    }
    h.ok(
        {temp["code"], other_scratch["code"]} <= with_scratch,
        "include_scratch muestra los temporales",
    )
    metrics = repo.stats(conn)
    h.ok(
        metrics["scratch"] >= 2
        and all(
            row["code"] != temp["code"]
            for row in repo.list_tickets(conn, status="en_progreso", limit=None)
        ),
        "stats cuenta los scratch aparte y no los mezcla",
    )
    stalled_codes = {row["code"] for row in audit_lib.stalled(conn, days=0)}
    h.ok(
        temp["code"] not in stalled_codes,
        "audit no marca un scratch como ticket estancado",
    )
    report = repo.confirm_tickets(conn, [temp["code"]])
    h.ok(
        not report["confirmed"]
        and temp["code"] in report["errors"],
        "confirm rechaza un ticket scratch",
    )

    db.meta_set(
        conn,
        scratch._meta_key(temp["code"]),
        "2000-01-01T00:00:00-05:00",
    )
    conn.commit()
    cleaned = scratch.clean(conn)
    h.ok(
        temp["code"] in cleaned
        and temp["code"]
        not in {row["code"] for row in scratch.list_scratch(conn)},
        "clean archiva los scratch caducados",
    )

    promoted = scratch.promote(conn, other_scratch["code"])
    h.ok(
        not scratch.is_scratch(promoted)
        and promoted["code"]
        in {row["code"] for row in repo.list_tickets(conn, limit=None)},
        "promote devuelve el scratch a la tabla",
    )
    dropped = scratch.create_or_reuse(
        conn, agent="opencode", session_id="ses_scratch_drop"
    )
    scratch.drop(conn, dropped["code"])
    h.ok(
        dropped["code"]
        not in {row["code"] for row in scratch.list_scratch(conn)},
        "drop archiva el scratch sin publicarlo",
    )

    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        code = cli.main(["scratch", "--list"])
    h.ok(
        code == 0 and "scratch" in out.getvalue().lower(),
        "taiki scratch --list responde por CLI",
    )
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        code = cli.main(["list", "--limit", "5"])
    listed_text = out.getvalue()
    h.ok(
        code == 0 and promoted["code"] in listed_text,
        "ticket list muestra el ticket promovido",
    )
