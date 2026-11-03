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
    h.ok(
        created["scope"] == "project"
        and created["status"] == "active"
        and created["ticket_kinds"] == ["bug"]
        and bool(created["evidence"]),
        "memory add crea una memoria de proyecto activa con etiquetas y evidencia",
    )

    with h.env(OPENCODE_SESSION_ID="ses_cli_test"):
        _, remembered = _json(
            ["remember", "Prefiero commits pequeños", "--json"]
        )
    h.ok(
        remembered["scope"] == "user"
        and remembered["kind"] == "preference"
        and remembered["source"] == "user"
        and remembered["evidence"][0]["ref"] == "ses_cli_test",
        "remember crea una preferencia de usuario con la sesión como evidencia",
    )

    _, listed = _json(["memory", "list", "--json"])
    _, by_ticket = _json(["memory", "list", "--ticket-kinds", "bug", "--json"])
    _, other_ticket = _json(
        ["memory", "list", "--ticket-kinds", "funcionalidad", "--json"]
    )
    h.ok(
        listed["count"] >= 2
        and any(item["code"] == created["code"] for item in by_ticket["memories"])
        and all(
            item["code"] != created["code"] for item in other_ticket["memories"]
        ),
        "memory list devuelve el contrato y filtra por tipo de ticket",
    )

    _, retagged = _json(
        [
            "memory",
            "edit",
            created["code"],
            "--ticket-kinds",
            "funcionalidad",
            "-t",
            "Rama de trabajo",
            "--json",
        ]
    )
    shown_code, shown_out, _ = _run(["memory", "show", created["code"]])
    h.ok(
        retagged["ticket_kinds"] == ["funcionalidad"]
        and retagged["title"] == "Rama de trabajo"
        and shown_code == 0
        and "Tipos de ticket: funcionalidad" in shown_out,
        "memory edit reemplaza etiquetas y título y show los imprime en texto",
    )

    _, shown = _json(["memory", "show", created["code"], "--json"])
    h.ok(
        "ledger" in shown
        and any(item["action"] == "create" for item in shown["ledger"])
        and "usage" in shown
        and "budget" in shown,
        "memory show incluye ledger, uso y presupuesto",
    )

    proposal = _proposal(h, "Propuesta CLI", "propuesta-cli")
    _, pending = _json(["memory", "pending", "--json"])
    failed, _, _ = _run(
        ["memory", "approve", proposal["code"], "--actor", "agente", "--json"]
    )
    still_proposed = _json(["memory", "show", proposal["code"], "--json"])[1][
        "status"
    ]
    h.ok(
        any(item["code"] == proposal["code"] for item in pending["memories"])
        and failed == 1
        and still_proposed == "proposed",
        "memory pending lista propuestas y solo el usuario puede aprobar",
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
    _, unpinned = _json(["memory", "unpin", created["code"], "--json"])
    h.ok(
        pinned["pinned"] and not unpinned["pinned"],
        "memory pin y unpin fijan y desfijan",
    )

    missing_why, _, _ = _run(["memory", "rm", created["code"]])
    _, removed = _json(
        ["memory", "rm", created["code"], "--why", "obsoleta", "--json"]
    )
    archived_status = _json(["memory", "show", created["code"], "--json"])[1][
        "status"
    ]
    h.ok(
        missing_why == 2
        and removed["status"] == "archived"
        and archived_status == "archived",
        "memory rm exige --why y archiva sin borrar",
    )

    too_long, _, err = _run(
        ["memory", "add", "--title", "Larga", "--content", "x" * 700]
    )
    h.ok(
        too_long == 1 and "demasiado largo" in err,
        "memory add respeta el máximo por entrada",
    )

    _, brief = _json(["brief", "--budget", "5000", "--json"])
    _, user_brief = _json(["brief", "--section", "user", "--json"])
    _, tight = _json(["brief", "--budget", "30", "--json"])
    h.ok(
        {"text", "usage", "sections", "version"} <= set(brief)
        and "## Usuario" in user_brief["text"]
        and "## Proyecto" not in user_brief["text"]
        and tight["usage"]["omitted"] >= 1
        and "entradas omitidas; usa memory_search" in tight["text"],
        "brief devuelve el contrato, filtra secciones y avisa de lo omitido",
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

    repo = Path(__file__).resolve().parents[1]
    memory_modules = sorted((repo / "ticketlib" / "memory").rglob("*.py"))
    memory_modules += sorted((repo / "commands").glob("memory*.py"))
    line_counts = {
        path.relative_to(repo).as_posix(): len(
            path.read_text(encoding="utf-8").splitlines()
        )
        for path in memory_modules
    }
    oversized = sorted(
        f"{name} ({lines})" for name, lines in line_counts.items() if lines > 700
    )
    h.ok(
        not oversized,
        "ningún módulo de memoria supera 700 líneas"
        + (f": {', '.join(oversized)}" if oversized else ""),
    )
