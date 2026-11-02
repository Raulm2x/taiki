"""Contrato del CLI de memoria: comandos, JSON, approve/reject y brief."""

from __future__ import annotations

import contextlib
import io
import json
from pathlib import Path

import cli
from ticketlib.memory import add_memory
from tests.support import Harness


def _run(argv: list[str]) -> tuple[int, str, str]:
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        try:
            code = cli.main(argv)
        except SystemExit as exc:  # argparse (por ejemplo, --why obligatorio)
            code = int(exc.code or 0)
    return code, out.getvalue(), err.getvalue()


def _json(argv: list[str]) -> tuple[int, dict]:
    code, out, err = _run(argv)
    if code != 0:
        raise AssertionError(f"CLI falló ({code}): {err}")
    return code, json.loads(out)


def _proposal(h: Harness, title: str, key: str) -> dict:
    return add_memory(
        h.conn,
        title=title,
        content="Propuesta pendiente de revisión.",
        kind="decision",
        key=key,
        source="agent",
        confidence=0.9,
        evidence=[{"kind": "file", "ref": "README.md"}],
    )


def run(h: Harness) -> None:
    _, created = _json(
        [
            "memory",
            "add",
            "--title",
            "Rama de integración",
            "--content",
            "develop integra con --ff-only.",
            "--kind",
            "convention",
            "--key",
            "rama-integracion",
            "--evidence",
            "README.md",
            "--ticket-kinds",
            "bug",
            "--json",
        ]
    )
    h.eq(created["scope"], "project", "memory add crea en el scope proyecto")
    h.eq(created["status"], "active", "una memoria de usuario nace activa")
    h.eq(
        created["ticket_kinds"],
        ["bug"],
        "memory add guarda --ticket-kinds",
    )
    h.ok(bool(created["evidence"]), "memory add guarda la evidencia indicada")

    with h.env(OPENCODE_SESSION_ID="ses_cli_test"):
        _, remembered = _json(
            ["remember", "Prefiero commits pequeños", "--json"]
        )
    h.eq(remembered["scope"], "user", "remember usa el scope usuario")
    h.eq(remembered["kind"], "preference", "remember guarda una preferencia")
    h.eq(remembered["source"], "user", "remember atribuye el origen al usuario")
    h.eq(remembered["status"], "active", "remember crea una preferencia activa")
    h.eq(
        remembered["evidence"][0]["ref"],
        "ses_cli_test",
        "remember usa la sesión como evidencia",
    )

    _, listed = _json(["memory", "list", "--json"])
    h.ok(listed["count"] >= 2, "memory list devuelve las memorias creadas")
    _, by_ticket = _json(["memory", "list", "--ticket-kinds", "bug", "--json"])
    _, other_ticket = _json(
        ["memory", "list", "--ticket-kinds", "funcionalidad", "--json"]
    )
    h.ok(
        any(item["code"] == created["code"] for item in by_ticket["memories"])
        and all(
            item["code"] != created["code"] for item in other_ticket["memories"]
        ),
        "memory list filtra por tipo de ticket y respeta las generales",
    )
    _, edited = _json(
        [
            "memory",
            "edit",
            created["code"],
            "--ticket-kinds",
            "funcionalidad",
            "--json",
        ]
    )
    h.eq(
        edited["ticket_kinds"],
        ["funcionalidad"],
        "memory edit reemplaza las etiquetas de ticket",
    )
    shown_code, shown_out, _ = _run(["memory", "show", created["code"]])
    h.ok(
        shown_code == 0 and "Tipos de ticket: funcionalidad" in shown_out,
        "memory show muestra los tipos de ticket en texto",
    )

    _, shown = _json(["memory", "show", created["code"], "--json"])
    h.ok(
        "ledger" in shown
        and any(item["action"] == "create" for item in shown["ledger"]),
        "memory show incluye el historial del ledger",
    )
    h.ok(
        "usage" in shown and "budget" in shown,
        "memory show incluye uso y presupuesto",
    )

    proposal = _proposal(h, "Propuesta CLI", "propuesta-cli")
    _, pending = _json(["memory", "pending", "--json"])
    h.ok(
        any(item["code"] == proposal["code"] for item in pending["memories"]),
        "memory pending lista las propuestas",
    )
    failed, _, _ = _run(
        ["memory", "approve", proposal["code"], "--actor", "agente", "--json"]
    )
    h.eq(failed, 1, "solo el usuario puede aprobar")
    h.eq(
        _json(["memory", "show", proposal["code"], "--json"])[1]["status"],
        "proposed",
        "un actor no usuario no activa la propuesta",
    )
    _, approved = _json(["memory", "approve", proposal["code"], "--json"])
    h.eq(approved["status"], "active", "el usuario activa la propuesta")

    rejected = _proposal(h, "Propuesta rechazada", "propuesta-rechazada")
    _, rejected_data = _json(
        ["memory", "reject", rejected["code"], "--why", "no aplica", "--json"]
    )
    h.eq(
        rejected_data["status"],
        "archived",
        "reject archiva la propuesta con motivo",
    )

    _, pinned = _json(["memory", "pin", created["code"], "--json"])
    h.ok(pinned["pinned"], "memory pin fija la memoria")
    _, unpinned = _json(["memory", "unpin", created["code"], "--json"])
    h.ok(not unpinned["pinned"], "memory unpin la desfija")

    _, edited = _json(
        ["memory", "edit", created["code"], "-t", "Rama de trabajo", "--json"]
    )
    h.eq(edited["title"], "Rama de trabajo", "memory edit cambia el título")

    missing_why, _, _ = _run(["memory", "rm", created["code"]])
    h.eq(missing_why, 2, "memory rm exige --why")
    _, removed = _json(
        ["memory", "rm", created["code"], "--why", "obsoleta", "--json"]
    )
    h.eq(removed["status"], "archived", "memory rm archiva en vez de borrar")
    h.eq(
        _json(["memory", "show", created["code"], "--json"])[1]["status"],
        "archived",
        "la memoria archivada sigue existiendo",
    )

    too_long, _, err = _run(
        ["memory", "add", "--title", "Larga", "--content", "x" * 700]
    )
    h.ok(
        too_long == 1 and "demasiado largo" in err,
        "memory add respeta el máximo por entrada",
    )

    _, brief = _json(["brief", "--budget", "5000", "--json"])
    h.ok(
        {"text", "usage", "sections", "version"} <= set(brief),
        "brief --json devuelve el contrato esperado",
    )
    _, user_brief = _json(["brief", "--section", "user", "--json"])
    h.ok(
        "## Usuario" in user_brief["text"]
        and "## Proyecto" not in user_brief["text"],
        "brief --section filtra la sección",
    )
    _, tight = _json(["brief", "--budget", "30", "--json"])
    h.ok(
        tight["usage"]["omitted"] >= 1
        and "entradas omitidas; usa memory_search" in tight["text"],
        "brief acota el contenido y avisa de lo omitido",
    )

    from commands import brief as brief_command

    original = brief_command.workspace.project_root
    brief_command.workspace.project_root = lambda: h.tmp
    try:
        write_code, write_out, _ = _run(["brief", "--write"])
    finally:
        brief_command.workspace.project_root = original
    artifact = Path(h.tmp) / ".taiki" / "generated" / "brief.md"
    h.ok(
        write_code == 0
        and artifact.is_file()
        and "generated by taiki; do not edit"
        in artifact.read_text(encoding="utf-8")
        and str(artifact) in write_out,
        "brief --write genera el artefacto y lo anuncia",
    )
