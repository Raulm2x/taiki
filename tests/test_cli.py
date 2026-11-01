"""Confirmación por lotes y acciones de administración."""

from __future__ import annotations

import contextlib
import io
import os
import subprocess
from pathlib import Path

import cli
from ticketlib import domain, repo
from tui import actions as tui_actions
from tests.support import Harness


def run(h: Harness) -> None:
    conn = h.conn
    tmp = str(h.tmp)

    # ── Confirmación múltiple y atómica ─────────────────────────────
    def completado(title: str) -> dict:
        created = repo.create_ticket(conn, title=title)
        repo.claim_ticket(conn, created["code"], agent="opencode")
        return repo.complete_ticket(
            conn, created["code"], resolution="Listo"
        )

    c1, c2, c3 = completado("uno"), completado("dos"), completado("tres")
    report = repo.confirm_tickets(conn, [c1["code"], c2["code"]])
    h.ok(
        report["confirmed"] == [c1["code"], c2["code"]],
        "confirm_tickets confirma varios a la vez",
    )
    h.ok(
        report["already"] == [] and report["errors"] == {},
        "un lote válido no reporta incidencias",
    )
    again = repo.confirm_tickets(conn, [c1["code"], c3["code"]])
    h.ok(
        again["already"] == [c1["code"]]
        and again["confirmed"] == [c3["code"]],
        "los ya confirmados se omiten sin romper el lote",
    )

    t_pending = repo.create_ticket(conn, title="Sin completar")
    c4, c5 = completado("cuatro"), completado("cinco")
    bad = repo.confirm_tickets(conn, [c4["code"], t_pending["code"]])
    h.ok(
        bad["confirmed"] == []
        and t_pending["code"] in bad["errors"],
        "un ticket no completado se reporta como error",
    )
    h.ok(
        repo.get_ticket(conn, c4["code"])["status"] == "completado",
        "un lote inválido no confirma ninguno",
    )
    missing = repo.confirm_tickets(conn, [c5["code"], "T-9999"])
    h.ok(
        missing["confirmed"] == [] and "T-9999" in missing["errors"],
        "un código inexistente se reporta como error",
    )
    h.ok(
        repo.get_ticket(conn, c5["code"])["status"] == "completado",
        "un código inexistente no confirma el resto",
    )
    dup = repo.confirm_tickets(conn, [c5["code"], c5["code"]])
    h.ok(
        dup["confirmed"] == [c5["code"]],
        "los códigos repetidos se deduplican",
    )
    h.raises(
        domain.ValidationError,
        lambda: repo.confirm_tickets(conn, []),
        "confirmar sin códigos falla",
    )

    tui_args = cli.build_parser().parse_args(
        ["tui", "--order", "priority", "--refresh", "0"]
    )
    h.ok(
        tui_args.order == "priority" and tui_args.refresh == 0,
        "el parser de la TUI acepta orden y refresco",
    )

    parsed = cli.build_parser().parse_args(
        ["confirm", "T-0001", "T-0002"]
    )
    h.ok(
        parsed.code == ["T-0001", "T-0002"],
        "el parser acepta varios códigos en confirm",
    )

    c6, c7 = completado("seis"), completado("siete")
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        exit_code = cli.main(["confirm", c6["code"], c7["code"]])
    h.ok(exit_code == 0, "el CLI confirma varios tickets")
    h.ok(
        c6["code"] in out.getvalue() and c7["code"] in out.getvalue(),
        "el CLI informa de cada ticket confirmado",
    )
    with contextlib.redirect_stdout(
        io.StringIO()
    ), contextlib.redirect_stderr(io.StringIO()):
        exit_bad = cli.main(["confirm", t_pending["code"]])
    h.ok(exit_bad == 1, "el CLI devuelve 1 si el lote es inválido")
    h.ok(
        repo.get_ticket(conn, t_pending["code"])["status"] == "nuevo",
        "el CLI no cambia nada en un lote inválido",
    )

    # ── Acciones de administración ────────────────────────────────
    target = repo.create_ticket(conn, title="Acciones de la TUI")
    claimed = tui_actions.claim(target["code"], "tui:test")
    h.ok(
        claimed["status"] == "en_progreso"
        and claimed["assigned_to"] == "tui:test",
        "la TUI puede reclamar un ticket",
    )
    assigned = tui_actions.assign(target["code"], "otro:agente", "usuario")
    h.ok(
        assigned["assigned_to"] == "otro:agente"
        and assigned["status"] == "en_progreso",
        "asignar no cambia el estado",
    )
    blocked = tui_actions.change_status(
        target["code"], "bloqueado", "esperando", "usuario"
    )
    h.ok(blocked["status"] == "bloqueado", "la TUI cambia de estado")
    h.raises(
        domain.TransitionError,
        lambda: tui_actions.change_status(
            target["code"], "completado", None, "usuario"
        ),
        "una transición inválida falla",
    )
    commented = tui_actions.comment(
        target["code"], "desde la TUI", "usuario"
    )
    h.ok(
        any(
            c["body"] == "desde la TUI"
            for c in commented["comments"]
        ),
        "la TUI añade comentarios",
    )
    h.raises(
        domain.ValidationError,
        lambda: tui_actions.comment(target["code"], "   ", "usuario"),
        "un comentario vacío falla",
    )
    linked = tui_actions.link_commit(target["code"], "abc1234", "usuario")
    h.ok(
        any(link["ref"] == "abc1234" for link in linked["links"]),
        "la TUI enlaza commits",
    )
    h.ok(
        tui_actions.available_statuses({"status": "completado"})
        == ["en_progreso"],
        "las acciones no ofrecen confirmar directamente",
    )
    h.ok(
        tui_actions.needs_note({"status": "completado"}, "en_progreso")
        and not tui_actions.needs_note({"status": "nuevo"}, "en_progreso"),
        "reabrir exige motivo",
    )
    payload = tui_actions.osc52("T-0001")
    h.ok(
        payload.startswith("\033]52;c;") and payload.endswith("\033\\"),
        "osc52 genera la secuencia de copiado",
    )

    # ── Wrapper del CLI ─────────────────────────────────────────────────
    root = Path(__file__).resolve().parent.parent
    env = {**os.environ, "TAIKI_QUIET": "1"}
    result = subprocess.run(
        [str(root / "bin" / "taiki"), "--help"],
        capture_output=True,
        text=True,
        env=env,
    )
    h.ok(
        result.returncode == 0 and "usage: taiki" in result.stdout,
        "bin/taiki arranca el CLI como taiki",
    )
