"""Contrato del CLI: flags, salida JSON, exit codes y acciones de la TUI.

La lógica de tickets vive en ``ticketlib``; aquí solo se comprueba la capa de
entrada/salida. Los lotes de confirmación se agrupan en un check por caso.
"""

from __future__ import annotations

import contextlib
import functools
import io
import json
import os
import subprocess
from pathlib import Path

import cli
import parser as cli_parser
from ticketlib import db, domain, repo
from tui import actions as tui_actions
from tests.support import Harness

ROOT = Path(__file__).resolve().parent.parent

# ``parse_args`` reconstruye el parser de argparse en cada invocación (≈40
# subcomandos). Es puro y reutilizable, así que la suite lo cachea una vez por
# proceso: los módulos CLI posteriores comparten proceso y heredan la caché.
cli_parser.build_parser = functools.cache(cli_parser.build_parser)


def _ok(h: Harness, *condiciones: tuple[bool, str]) -> None:
    """Agrupa aserciones en un check: todas se evalúan y se conserva el motivo."""
    fallos = [mensaje for cumple, mensaje in condiciones if not cumple]
    h.ok(not fallos, " / ".join(fallos))


def _falla(func, exc) -> bool:
    """True si la llamada lanza la excepción esperada."""
    try:
        func()
    except exc:
        return True
    return False


def run(h: Harness) -> None:
    conn = h.conn

    # ── Confirmación múltiple y atómica ─────────────────────────────
    def completado(title: str) -> dict:
        created = repo.create_ticket(conn, title=title)
        repo.claim_ticket(conn, created["code"], agent="opencode")
        return repo.complete_ticket(
            conn, created["code"], resolution="Listo"
        )

    c1, c2, c3 = completado("uno"), completado("dos"), completado("tres")
    report = repo.confirm_tickets(conn, [c1["code"], c2["code"]])
    again = repo.confirm_tickets(conn, [c1["code"], c3["code"]])
    t_pending = repo.create_ticket(conn, title="Sin completar")
    c4, c5 = completado("cuatro"), completado("cinco")
    bad = repo.confirm_tickets(conn, [c4["code"], t_pending["code"]])
    missing = repo.confirm_tickets(conn, [c5["code"], "T-9999"])
    c4_status = repo.get_ticket(conn, c4["code"])["status"]
    c5_status = repo.get_ticket(conn, c5["code"])["status"]
    dup = repo.confirm_tickets(conn, [c5["code"], c5["code"]])
    _ok(
        h,
        (
            report["confirmed"] == [c1["code"], c2["code"]]
            and report["already"] == []
            and report["errors"] == {},
            "confirm_tickets confirma varios a la vez sin incidencias",
        ),
        (
            again["already"] == [c1["code"]]
            and again["confirmed"] == [c3["code"]],
            "los ya confirmados se omiten sin romper el lote",
        ),
        (
            bad["confirmed"] == []
            and t_pending["code"] in bad["errors"]
            and missing["confirmed"] == []
            and "T-9999" in missing["errors"],
            "un ticket no completado o inexistente se reporta como error",
        ),
        (
            c4_status == "completado" and c5_status == "completado",
            "un lote inválido no confirma ninguno de sus tickets válidos",
        ),
        (
            dup["confirmed"] == [c5["code"]]
            and _falla(
                lambda: repo.confirm_tickets(conn, []),
                domain.ValidationError,
            ),
            "los códigos repetidos se deduplican y confirmar sin códigos falla",
        ),
    )

    # ── Parser ─────────────────────────────────────────────────────
    tui_args = cli.build_parser().parse_args(
        ["tui", "--order", "priority", "--refresh", "0"]
    )
    parsed = cli.build_parser().parse_args(
        ["confirm", "T-0001", "T-0002"]
    )
    _ok(
        h,
        (
            tui_args.order == "priority" and tui_args.refresh == 0,
            "el parser de la TUI acepta orden y refresco",
        ),
        (
            parsed.code == ["T-0001", "T-0002"],
            "el parser acepta varios códigos en confirm",
        ),
    )

    # ── confirm: varios códigos, exit code y sin cambios si falla ──
    c6, c7 = completado("seis"), completado("siete")
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        exit_code = cli.main(["confirm", c6["code"], c7["code"]])
    with contextlib.redirect_stdout(
        io.StringIO()
    ), contextlib.redirect_stderr(io.StringIO()):
        exit_bad = cli.main(["confirm", t_pending["code"]])
    _ok(
        h,
        (exit_code == 0, "el CLI confirma varios tickets"),
        (
            c6["code"] in out.getvalue() and c7["code"] in out.getvalue(),
            "el CLI informa de cada ticket confirmado",
        ),
        (exit_bad == 1, "el CLI devuelve 1 si el lote es inválido"),
        (
            repo.get_ticket(conn, t_pending["code"])["status"] == "nuevo",
            "el CLI no cambia nada en un lote inválido",
        ),
    )

    # ── transition no puede confirmar (ni por proceso) ─────────────
    bypass = completado("bypass de confirmación")
    with contextlib.redirect_stderr(io.StringIO()):
        bypass_code = cli.main(
            ["transition", bypass["code"], "confirmado"]
        )
    _ok(
        h,
        (
            bypass_code == 1
            and repo.get_ticket(conn, bypass["code"])["status"]
            == "completado",
            "taiki transition no puede confirmar",
        ),
    )

    verified = completado("verificado por CLI")
    repo.transition(
        conn,
        verified["code"],
        "verificado",
        note="tests/run.py --strict-budget verde",
        actor="conductor",
    )
    with contextlib.redirect_stdout(io.StringIO()):
        exit_verified = cli.main(["confirm", verified["code"]])
    _ok(
        h,
        (
            exit_verified == 0
            and repo.get_ticket(conn, verified["code"])["status"]
            == "confirmado",
            "el CLI confirma un ticket verificado",
        ),
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
    _ok(
        h,
        (
            exit_reject == 0
            and repo.get_ticket(conn, rejected["code"])["iterations"] == 2
            and "intento 2" in out.getvalue(),
            "rechazar un verificado cuenta un intento y lo informa",
        ),
    )

    # ── Acciones de administración ────────────────────────────────
    target = repo.create_ticket(conn, title="Acciones de la TUI")
    claimed = tui_actions.claim(target["code"], "tui:test")
    assigned = tui_actions.assign(target["code"], "otro:agente", "usuario")
    blocked = tui_actions.change_status(
        target["code"], "bloqueado", "esperando", "usuario"
    )
    commented = tui_actions.comment(
        target["code"], "desde la TUI", "usuario"
    )
    linked = tui_actions.link_commit(target["code"], "abc1234", "usuario")
    payload = tui_actions.osc52("T-0001")
    _ok(
        h,
        (
            claimed["status"] == "en_progreso"
            and claimed["assigned_to"] == "tui:test"
            and assigned["assigned_to"] == "otro:agente"
            and assigned["status"] == "en_progreso"
            and blocked["status"] == "bloqueado",
            "la TUI reclama, asigna y cambia de estado",
        ),
        (
            _falla(
                lambda: tui_actions.change_status(
                    target["code"], "completado", None, "usuario"
                ),
                domain.TransitionError,
            ),
            "una transición inválida falla",
        ),
        (
            any(
                c["body"] == "desde la TUI"
                for c in commented["comments"]
            )
            and any(link["ref"] == "abc1234" for link in linked["links"]),
            "la TUI añade comentarios y enlaza commits",
        ),
        (
            _falla(
                lambda: tui_actions.comment(target["code"], "   ", "usuario"),
                domain.ValidationError,
            ),
            "un comentario vacío falla",
        ),
        (
            tui_actions.available_statuses({"status": "completado"})
            == ["bloqueado", "en_progreso", "verificado"]
            and "confirmado"
            not in tui_actions.available_statuses({"status": "verificado"})
            and tui_actions.needs_note(
                {"status": "completado"}, "en_progreso"
            )
            and not tui_actions.needs_note(
                {"status": "nuevo"}, "en_progreso"
            ),
            "verificar no confirma y reabrir exige motivo",
        ),
        (
            payload.startswith("\033]52;c;")
            and payload.endswith("\033\\"),
            "osc52 genera la secuencia de copiado",
        ),
    )

    # ── JSON compacto y resumido ──────────────────────────────────
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        cli.main(["list", "--json-summary", "--compact", "--limit", "5"])
    text = out.getvalue().strip()
    data = json.loads(text)
    _ok(
        h,
        (
            data["count"] <= 5
            and all("body" not in ticket for ticket in data["tickets"])
            and "\n" not in text,
            "list --json-summary --compact emite una línea sin historial",
        ),
    )
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        cli.main(["show", c1["code"], "--json", "--compact"])
    shown = out.getvalue().strip()
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        cli.main(["show", c1["code"], "--compact"])
    compact_show = out.getvalue().strip()
    _ok(
        h,
        (
            "\n" not in shown and json.loads(shown)["code"] == c1["code"],
            "show --json --compact emite JSON en una línea",
        ),
        (
            "\n" not in compact_show
            and json.loads(compact_show)["code"] == c1["code"],
            "show --compact implica --json y no se ignora en silencio",
        ),
    )
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        cli.main(["show", c1["code"], "--json"])
    indented_show = out.getvalue().strip()
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        cli.main(["list", "--json", "--limit", "5"])
    pretty = out.getvalue().strip()
    pretty_data = json.loads(pretty)
    _ok(
        h,
        ("\n" in indented_show, "show --json emite JSON con sangría por defecto"),
        (
            "\n" in pretty
            and all(
                "session_id" in ticket for ticket in pretty_data["tickets"]
            ),
            "list --json emite JSON con sangría y sesión por ticket",
        ),
    )
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        cli.main(["list", "--json-summary", "--limit", "5"])
    summary_data = json.loads(out.getvalue().strip())
    _ok(
        h,
        (
            all(
                "body" not in ticket and "session_id" not in ticket
                for ticket in summary_data["tickets"]
            ),
            "list --json-summary implica --json y omite sesiones y cuerpo",
        ),
    )
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        cli.main(["list", "--compact", "--limit", "5"])
    compact_only = json.loads(out.getvalue().strip())
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        cli.main(["list", "--json", "--compact", "--limit", "5"])
    json_compact = json.loads(out.getvalue().strip())
    _ok(
        h,
        (
            compact_only["tickets"] and json_compact["tickets"],
            "hay tickets abiertos para comparar --compact",
        ),
        (
            {key for ticket in compact_only["tickets"] for key in ticket}
            == {key for ticket in json_compact["tickets"] for key in ticket},
            "--compact y --json --compact devuelven las mismas claves",
        ),
        (
            all("body" in ticket for ticket in compact_only["tickets"]),
            "--compact incluye body como --json",
        ),
    )

    # ── Wrapper del CLI ─────────────────────────────────────────────────
    env = {**os.environ, "TAIKI_QUIET": "1"}
    result = subprocess.run(
        [str(ROOT / "bin" / "taiki"), "--help"],
        capture_output=True,
        text=True,
        env=env,
    )
    _ok(
        h,
        (
            result.returncode == 0 and "usage: taiki" in result.stdout,
            "bin/taiki arranca el CLI como taiki",
        ),
    )

    # ── Aislamiento y scope por CLI ─────────────────────────────────────
    cli_db = str(h.tmp / "cli-isolation.db")
    out = io.StringIO()
    with h.env(TAIKI_DB=cli_db), contextlib.redirect_stdout(out):
        created_code = cli.main(
            [
                "new",
                "-t",
                "Ticket CLI aislado",
                "--isolation",
                "scope",
                "--resource",
                "package:git",
                "--resource",
                "systemd:user/taiki.service",
                "--reversal",
                '{"kind": "manual", "ref": "revertir"}',
            ]
        )
    iso_conn = db.connect(cli_db)
    try:
        created_ticket = repo.get_ticket(iso_conn, "T-0001")
        out = io.StringIO()
        with h.env(TAIKI_DB=cli_db), contextlib.redirect_stdout(out):
            updated_code = cli.main(
                ["update", "T-0001", "--isolation", "none"]
            )
        _ok(
            h,
            (
                created_code == 0
                and (
                    created_ticket["isolation"],
                    created_ticket["resources"],
                    created_ticket["reversal"]["kind"],
                )
                == (
                    "scope",
                    ["package:git", "systemd:user/taiki.service"],
                    "manual",
                ),
                "new acepta y persiste aislamiento, recursos y reversión",
            ),
            (
                updated_code == 0
                and repo.get_ticket(iso_conn, "T-0001")["isolation"]
                == "none",
                "update acepta --isolation y lo cambia",
            ),
        )
        out = io.StringIO()
        with h.env(TAIKI_DB=cli_db), contextlib.redirect_stdout(out):
            cli.main(["show", "T-0001", "--json", "--compact"])
        shown = json.loads(out.getvalue().strip())
        out = io.StringIO()
        with h.env(TAIKI_DB=cli_db), contextlib.redirect_stdout(out):
            cli.main(
                ["list", "--json-summary", "--compact", "--limit", "5"]
            )
        listed = json.loads(out.getvalue().strip())
        _ok(
            h,
            (
                shown["isolation"] == "none"
                and shown["resources"]
                == ["package:git", "systemd:user/taiki.service"]
                and isinstance(shown.get("scope"), list)
                and shown["scope"],
                "show incluye isolation, recursos y scope",
            ),
            (
                listed.get("scope_roots")
                and all(
                    "isolation" in item for item in listed["tickets"]
                ),
                "list incluye isolation por ticket y scope_roots",
            ),
        )
    finally:
        iso_conn.close()

    # ── Reorganización por CLI con dry-run por defecto ──────────────
    reorg_epic = repo.create_ticket(conn, title="Épica CLI destino")
    reorg_flat = repo.create_ticket(conn, title="Tarea CLI plana")
    conn.execute(
        "UPDATE tickets SET code = 'T-9201' WHERE id = ?",
        (reorg_flat["id"],),
    )
    conn.commit()
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        dry_move = cli.main(
            ["move", "T-9201", "--parent", reorg_epic["code"], "--renumber"]
        )
    unchanged = repo.get_ticket(conn, "T-9201")
    json_out = io.StringIO()
    with contextlib.redirect_stdout(json_out):
        cli.main(
            [
                "move",
                "T-9201",
                "--parent",
                reorg_epic["code"],
                "--keep-code",
                "--json",
            ]
        )
    payload = json.loads(json_out.getvalue())
    _ok(
        h,
        (
            dry_move == 0 and "T-9201 → " in out.getvalue(),
            "move simula con renumber y muestra OLD → NEW",
        ),
        (
            unchanged["code"] == "T-9201"
            and unchanged["parent_id"] is None,
            "move sin --apply no escribe",
        ),
        (
            payload["action"] == "move"
            and payload["items"][0]["new_code"] == "T-9201",
            "move --json devuelve el plan y keep-code conserva el código",
        ),
    )
    with contextlib.redirect_stdout(io.StringIO()):
        applied_move = cli.main(
            [
                "move",
                "T-9201",
                "--parent",
                reorg_epic["code"],
                "--renumber",
                "--apply",
            ]
        )
    moved = repo.get_ticket(conn, reorg_epic["code"] + ".01")
    _ok(
        h,
        (
            applied_move == 0 and moved["id"] == reorg_flat["id"],
            "move --renumber --apply deja padre.NN",
        ),
        (
            moved["parent_id"] == reorg_epic["id"],
            "move --apply actualiza el padre",
        ),
    )

    descendant = repo.create_ticket(
        conn, title="Hija de la épica CLI", parent_id=reorg_epic["code"]
    )
    err = io.StringIO()
    with contextlib.redirect_stderr(err):
        bad_move = cli.main(
            [
                "move",
                reorg_epic["code"],
                "--parent",
                descendant["code"],
                "--renumber",
            ]
        )
    _ok(
        h,
        (
            bad_move == 1 and "Error:" in err.getvalue(),
            "move rechaza ciclos/dos niveles con exit != 0",
        ),
    )

    promote_task = repo.create_ticket(
        conn, title="Tarea promovible CLI", parent_id=reorg_epic["code"]
    )
    with contextlib.redirect_stdout(io.StringIO()):
        dry_promote = cli.main(["promote", promote_task["code"]])
    still_child = repo.get_ticket(conn, promote_task["code"])
    with contextlib.redirect_stdout(io.StringIO()):
        applied_promote = cli.main(
            ["promote", promote_task["code"], "--apply"]
        )
    promoted = repo.get_ticket(conn, promote_task["code"])
    _ok(
        h,
        (
            dry_promote == 0
            and still_child["parent_id"] == reorg_epic["id"],
            "promote simula sin escribir",
        ),
        (
            applied_promote == 0 and promoted["parent_id"] is None,
            "promote --apply deja la tarea en la raíz",
        ),
    )

    demote_epic = repo.create_ticket(conn, title="Épica a degradar CLI")
    demote_child = repo.create_ticket(
        conn, title="Tarea reasignada CLI", parent_id=demote_epic["code"]
    )
    dest_epic = repo.create_ticket(conn, title="Épica destino CLI")
    with contextlib.redirect_stdout(io.StringIO()):
        applied_demote = cli.main(
            [
                "demote",
                demote_epic["code"],
                "--children-to",
                dest_epic["code"],
                "--parent",
                reorg_epic["code"],
                "--renumber",
                "--apply",
            ]
        )
    demoted = repo.get_ticket(conn, demote_epic["code"])
    renumbered_child = repo.get_ticket(conn, dest_epic["code"] + ".01")
    _ok(
        h,
        (
            applied_demote == 0
            and demoted["parent_id"] == reorg_epic["id"],
            "demote --apply mueve la épica al padre indicado",
        ),
        (
            renumbered_child["id"] == demote_child["id"],
            "demote --renumber numera los hijos bajo su nuevo padre",
        ),
    )

    ren_epic = repo.create_ticket(conn, title="Épica adoptada CLI")
    adopted = repo.create_ticket(conn, title="Ticket adoptado CLI")
    watcher = repo.create_ticket(conn, title="Vigilante CLI")
    repo.attach_tickets(
        conn, ren_epic["code"], [adopted["code"]], actor="test"
    )
    repo.link_ticket(
        conn, watcher["code"], "depende", adopted["code"], actor="test"
    )
    with contextlib.redirect_stdout(io.StringIO()):
        applied_ren = cli.main(
            ["renumber", adopted["code"], "--adopted", "--apply"]
        )
    new_adopted_code = ren_epic["code"] + ".01"
    renamed = repo.get_ticket(conn, new_adopted_code)
    link_refs = [
        link["ref"]
        for link in repo.get_ticket(conn, watcher["code"])["links"]
        if link["kind"] == "depende"
    ]
    _ok(
        h,
        (
            applied_ren == 0 and renamed["id"] == adopted["id"],
            "renumber --adopted renombra el ticket adoptado",
        ),
        (
            new_adopted_code in link_refs,
            "renumber --adopted reapunta el enlace depende",
        ),
    )

    attach_epic = repo.create_ticket(conn, title="Épica attach CLI")
    attach_flat = repo.create_ticket(conn, title="Tarea attach CLI")
    conn.execute(
        "UPDATE tickets SET code = 'T-9202' WHERE id = ?",
        (attach_flat["id"],),
    )
    conn.commit()
    with contextlib.redirect_stdout(io.StringIO()):
        attach_exit = cli.main(["attach", attach_epic["code"], "T-9202"])
    attached = repo.get_ticket(conn, "T-9202")
    _ok(
        h,
        (
            attach_exit == 0
            and attached["parent_id"] == attach_epic["id"]
            and attached["code"] == "T-9202",
            "attach sin flags vincula conservando el código",
        ),
    )

    # ── Purge por CLI: dry-run, --force y JSON ──────────────────────
    def cancelado_cli(title: str) -> dict:
        created = repo.create_ticket(conn, title=title)
        conn.execute(
            "UPDATE tickets SET status = 'cancelado' WHERE id = ?",
            (created["id"],),
        )
        conn.commit()
        return repo.get_ticket(conn, created["code"])

    purga = cancelado_cli("Purga CLI")
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        dry_purge = cli.main(["purge", purga["code"]])
    sigue = repo.get_ticket(conn, purga["code"])

    json_purga = cancelado_cli("Purga CLI json")
    json_out = io.StringIO()
    with contextlib.redirect_stdout(json_out):
        json_exit = cli.main(["purge", json_purga["code"], "--json"])
    payload = json.loads(json_out.getvalue())

    err = io.StringIO()
    with contextlib.redirect_stderr(err):
        sin_force = cli.main(["purge", purga["code"], "--apply"])
    sigue_tras = repo.get_ticket(conn, purga["code"])

    with contextlib.redirect_stdout(io.StringIO()):
        applied_purge = cli.main(
            ["purge", purga["code"], "--apply", "--force"]
        )
    borrado = conn.execute(
        "SELECT COUNT(*) FROM tickets WHERE code = ?", (purga["code"],)
    ).fetchone()[0]
    _ok(
        h,
        (
            dry_purge == 0
            and "Purga CLI" in out.getvalue()
            and sigue["status"] == "cancelado",
            "purge simula por defecto y muestra el ticket",
        ),
        (
            json_exit == 0
            and payload["items"][0]["code"] == json_purga["code"]
            and payload["counter"]["before"] >= payload["counter"]["after"],
            "purge --json devuelve el plan y el contador",
        ),
        (
            sin_force == 1
            and "Error:" in err.getvalue()
            and sigue_tras["id"] == purga["id"],
            "purge --apply sin --force falla y no borra",
        ),
        (
            applied_purge == 0 and borrado == 0,
            "purge --apply --force borra el ticket cancelado",
        ),
    )

