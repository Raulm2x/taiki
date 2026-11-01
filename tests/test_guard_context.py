"""Guard jerárquico: la épica abierta como contexto del ticket."""

from __future__ import annotations

import contextlib
import io
import json
import sys
from pathlib import Path

import cli
from ticketlib import db, guard, repo, scratch
from tests.support import Harness


def run(h: Harness) -> None:
    conn = h.conn
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

    # Por defecto la épica NO atrae trabajo visible: el guard crea scratch.
    counter_before = db.meta_get(conn, "next_ticket_number")
    default_with_epic = guard.evaluate(
        tool="write",
        session="ses_guard_epic",
        agent="opencode",
        path=str(Path.home() / ".config/x"),
    )
    default_ticket = repo.get_ticket(conn, default_with_epic["ticket"])
    h.ok(
        default_with_epic["action"] == "created"
        and scratch.is_scratch(default_ticket)
        and scratch.CODE_PREFIX in default_ticket["code"],
        "por defecto el guard crea un scratch aunque haya épica abierta",
    )
    h.eq(
        db.meta_get(conn, "next_ticket_number"),
        counter_before,
        "el ticket por defecto no consume números T-XXXX",
    )
    scratch.drop(conn, default_ticket["code"])

    h.eq(
        guard.auto_mode(environ={}),
        "scratch",
        "sin variable ni config la política es scratch",
    )
    h.eq(
        guard.auto_mode(environ={}, config={"guard_auto": "auto"}),
        "auto",
        "config.json guard_auto fija la política",
    )
    h.eq(
        guard.auto_mode(
            environ={"TAIKI_GUARD_AUTO": "ticket"},
            config={"guard_auto": "auto"},
        ),
        "ticket",
        "la variable de entorno gana a la config",
    )
    h.eq(
        guard.auto_mode(environ={}, config={"guard_auto": "inventado"}),
        "scratch",
        "un guard_auto inválido cae al default seguro",
    )

    nested = guard.evaluate(
        tool="write",
        session="ses_guard_epic",
        agent="opencode",
        path=str(Path.home() / ".config/x"),
        environ={"TAIKI_GUARD_AUTO": "auto"},
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
    config_dir = Path.home() / ".config"
    hook_path = str(config_dir / "hook.txt")
    parsed = guard.parse_hook_input(
        {
            "tool_name": "write_file",
            "tool_input": {"file_path": str(config_dir / "x")},
            "session_id": "ses_hook",
            "agent_name": "antigravity",
        }
    )
    h.ok(
        parsed["tool"] == "write_file"
        and parsed["path"] == str(config_dir / "x")
        and parsed["agent"] == "antigravity",
        "parse_hook_input extrae el evento genérico",
    )

    hook_event = json.dumps(
        {
            "tool_name": "write_file",
            "tool_input": {"path": hook_path},
            "session_id": "ses_hook_a",
        }
    )
    out = io.StringIO()
    saved_stdin = sys.stdin
    try:
        sys.stdin = io.StringIO(hook_event)
        with contextlib.redirect_stdout(out):
            code = cli.main(["guard", "--hook"])
        payload = json.loads(out.getvalue())
        h.ok(
            code == 2
            and payload["decision"] == "deny"
            and payload["ticket"],
            "el modo hook bloquea y crea el ticket",
        )
        sys.stdin = io.StringIO(hook_event)
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

