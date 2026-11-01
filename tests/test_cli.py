"""Confirmación por lotes y acciones de administración."""

from __future__ import annotations

import contextlib
import io
import json
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

    verified = completado("verificado por CLI")
    repo.transition(
        conn,
        verified["code"],
        "verificado",
        note="tests/run.py --strict-budget verde",
        actor="conductor",
    )
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        exit_verified = cli.main(["confirm", verified["code"]])
    h.ok(
        exit_verified == 0
        and repo.get_ticket(conn, verified["code"])["status"] == "confirmado",
        "el CLI confirma un ticket verificado",
    )

    rejected = completado("rechazado por CLI")
    repo.transition(
        conn,
        rejected["code"],
        "verificado",
        note="gates verdes",
        actor="conductor",
    )
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        exit_reject = cli.main(
            ["reject", rejected["code"], "--why", "caso sin cubrir"]
        )
    h.ok(
        exit_reject == 0
        and repo.get_ticket(conn, rejected["code"])["iterations"] == 2
        and "intento 2" in out.getvalue(),
        "rechazar un verificado cuenta un intento y lo informa",
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
        == ["bloqueado", "en_progreso", "verificado"],
        "las acciones ofrecen verificar pero no confirmar directamente",
    )
    h.ok(
        "confirmado"
        not in tui_actions.available_statuses({"status": "verificado"}),
        "confirmar sigue reservado al usuario",
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

    # ── JSON compacto y resumido ────────────────────────────────────────
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        cli.main(["list", "--json-summary", "--compact", "--limit", "5"])
    text = out.getvalue().strip()
    data = json.loads(text)
    h.ok(
        data["count"] <= 5
        and all("body" not in ticket for ticket in data["tickets"])
        and "\n" not in text,
        "list --json-summary --compact emite una línea sin historial",
    )
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        cli.main(["show", c1["code"], "--json", "--compact"])
    shown = out.getvalue().strip()
    h.ok(
        "\n" not in shown and json.loads(shown)["code"] == c1["code"],
        "show --json --compact emite JSON en una línea",
    )
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        cli.main(["show", c1["code"], "--compact"])
    shown = out.getvalue().strip()
    h.ok(
        "\n" not in shown and json.loads(shown)["code"] == c1["code"],
        "show --compact implica --json y no se ignora en silencio",
    )
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        cli.main(["show", c1["code"], "--json"])
    h.ok(
        "\n" in out.getvalue().strip(),
        "show --json emite JSON con sangría por defecto",
    )
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        cli.main(["list", "--json", "--limit", "5"])
    pretty = out.getvalue().strip()
    pretty_data = json.loads(pretty)
    h.ok(
        "\n" in pretty
        and all("session_id" in ticket for ticket in pretty_data["tickets"]),
        "list --json emite JSON con sangría y sesión por ticket",
    )
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        cli.main(["list", "--json-summary", "--limit", "5"])
    summary_data = json.loads(out.getvalue().strip())
    h.ok(
        all(
            "body" not in ticket and "session_id" not in ticket
            for ticket in summary_data["tickets"]
        ),
        "list --json-summary implica --json y omite sesiones y cuerpo",
    )
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        cli.main(["list", "--compact", "--limit", "5"])
    compact_only = json.loads(out.getvalue().strip())
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        cli.main(["list", "--json", "--compact", "--limit", "5"])
    json_compact = json.loads(out.getvalue().strip())
    h.ok(
        compact_only["tickets"] and json_compact["tickets"],
        "hay tickets abiertos para comparar --compact",
    )
    h.eq(
        {key for ticket in compact_only["tickets"] for key in ticket},
        {key for ticket in json_compact["tickets"] for key in ticket},
        "--compact y --json --compact devuelven las mismas claves",
    )
    h.ok(
        all("body" in ticket for ticket in compact_only["tickets"]),
        "--compact incluye body como --json",
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
