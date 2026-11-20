"""Contrato del CLI de candidatos y revisión: JSON, filtros y artefactos."""

from __future__ import annotations

import contextlib
import io
import json
from pathlib import Path

import cli
from ticketlib import repo
from ticketlib.memory import add_memory
from tests.support import Harness


def _run(argv: list[str]) -> tuple[int, str, str]:
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        try:
            code = cli.main(argv)
        except SystemExit as exc:
            code = int(exc.code or 0)
    return code, out.getvalue(), err.getvalue()


def _json(argv: list[str]) -> tuple[int, dict]:
    code, out, err = _run(argv)
    if code != 0:
        raise AssertionError(f"CLI falló ({code}): {err}")
    return code, json.loads(out)


def _rework(conn, title: str, why: str, *, kind: str = "funcionalidad") -> dict:
    ticket = repo.create_ticket(conn, title=title, kind=kind)
    repo.transition(
        conn, ticket["code"], "en_progreso", actor="worker", actor_type="agente"
    )
    repo.complete_ticket(
        conn, ticket["code"], resolution="primera entrega", actor="worker"
    )
    repo.transition(
        conn,
        ticket["code"],
        "verificado",
        note="gates ok",
        actor="conductor",
        actor_type="agente",
    )
    repo.transition(
        conn,
        ticket["code"],
        "en_progreso",
        note=why,
        actor="usuario",
        actor_type="usuario",
    )
    return ticket


def _iterated(conn, title: str) -> dict:
    """Ticket con dos intentos: suficiente para que el motor lo agrupe."""
    ticket = repo.create_ticket(conn, title=title)
    conn.execute(
        "UPDATE tickets SET iterations = 2 WHERE id = ?", (ticket["id"],)
    )
    return ticket


def _candidates(h: Harness) -> None:
    rework = _rework(h.conn, "Rework CLI", "faltó el caso límite")
    rejection = _rework(h.conn, "Rechazo CLI", "faltó el test", kind="bug")
    repo.comment_ticket(
        h.conn,
        rejection["code"],
        "Rechazado en revisión: falta el test",
        author="usuario",
        actor_type="usuario",
    )

    code, payload = _json(["memory", "candidates", "--json"])
    h.ok(
        code == 0
        and set(payload) == {"count", "candidates"}
        and payload["count"] == len(payload["candidates"]),
        "memory candidates devuelve un JSON estable con count coherente",
    )
    rework_candidate = next(
        item for item in payload["candidates"] if item["rule"] == "rework"
    )
    h.ok(
        rework_candidate["kind_sugerido"] == "pitfall"
        and any(
            item["kind"] == "ticket" and item["ref"] == rework["code"]
            for item in rework_candidate["evidence"]
        )
        and rework_candidate["sample_size"] >= 1
        and 0.0 <= rework_candidate["confidence"] <= 1.0,
        "el candidato expone tipo, evidencia, muestra y confianza",
    )

    human_code, human, _ = _run(["memory", "candidates"])
    h.ok(
        human_code == 0
        and "rework" in human
        and "Evidencia:" in human
        and "Muestra:" in human,
        "la salida humana muestra regla, evidencia y muestra",
    )

    _, pitfall = _json(["memory", "candidates", "--kind", "pitfall", "--json"])
    h.ok(
        pitfall["count"] >= 1
        and all(
            item["kind_sugerido"] == "pitfall"
            for item in pitfall["candidates"]
        ),
        "--kind filtra candidatos del tipo pedido",
    )
    _, limited = _json(["memory", "candidates", "--limit", "1", "--json"])
    _, combined = _json(
        ["memory", "candidates", "--kind", "pitfall", "--limit", "1", "--json"]
    )
    h.ok(
        limited["count"] == 1 and combined["count"] == 1,
        "--limit acota la lista y se aplica tras --kind",
    )
    bad_limit, _, err = _run(["memory", "candidates", "--limit", "0"])
    h.ok(bad_limit == 1 and "--limit" in err, "--limit inválido da error claro")
    _, windowed = _json(["memory", "candidates", "--since", "30d", "--json"])
    invalid, _, since_err = _run(["memory", "candidates", "--since", "0d"])
    h.ok(
        windowed["count"] >= 1 and invalid == 1 and "--since" in since_err,
        "--since acepta una ventana en días y rechaza una inválida",
    )

    for index in range(21):
        _iterated(h.conn, f"Ciclo {index}")
    _, default = _json(["memory", "candidates", "--json"])
    _, everything = _json(["memory", "candidates", "--all", "--json"])
    h.ok(
        default["count"] == 20 and everything["count"] > default["count"],
        "sin --all se respeta candidate_max y --all lo ignora",
    )


def _review(h: Harness) -> None:
    code, packet = _json(["memory", "review", "--json"])
    h.ok(
        code == 0
        and set(packet)
        == {
            "generated_at",
            "candidates",
            "current_memories",
            "recent_tickets",
            "thresholds",
            "rules",
            "instructions",
        }
        and bool(packet["candidates"])
        and bool(packet["instructions"]),
        "el paquete de revisión conserva su contrato",
    )

    human_code, human, _ = _run(["memory", "review"])
    h.ok(
        human_code == 0
        and "Revisión de memoria" in human
        and "Instrucciones:" in human,
        "la revisión humana resume el paquete",
    )

    from commands import memory as memory_command

    original = memory_command.workspace.project_root
    memory_command.workspace.project_root = lambda: h.tmp
    try:
        write_code, write_out, _ = _run(["memory", "review", "--write"])
        artifact = Path(h.tmp) / ".taiki" / "generated" / "memory-review.json"
        stored = json.loads(artifact.read_text(encoding="utf-8"))
        leftovers = list(
            (Path(h.tmp) / ".taiki" / "generated").glob(".memory-review-*")
        )
        h.ok(
            write_code == 0
            and artifact.is_file()
            and "Escrito:" in write_out
            and set(stored) == set(packet)
            and bool(stored["generated_at"])
            and leftovers == [],
            "review --write crea el artefacto con el contrato y sin temporales",
        )
        _, written = _json(["memory", "review", "--write", "--json"])
        h.ok(
            written["path"] == str(artifact) and "path" not in stored,
            "review --json --write anuncia la ruta sin ensuciar el artefacto",
        )
    finally:
        memory_command.workspace.project_root = original


def _pending_diff(h: Harness) -> None:
    evidence = repo.create_ticket(h.conn, title="Evidencia de diff")
    old = add_memory(
        h.conn,
        title="Memoria vieja",
        content="contenido viejo",
        key="diff-key",
        source="user",
        actor="usuario",
    )
    new = add_memory(
        h.conn,
        title="Memoria nueva",
        content="contenido nuevo",
        key="diff-key",
        source="agent",
        actor="worker",
        evidence=[evidence["code"]],
    )

    _, plain = _json(["memory", "pending", "--json"])
    stored = next(item for item in plain["memories"] if item["code"] == new["code"])
    h.ok("diff" not in stored, "pending sin --diff no añade ruido")

    _, diffed = _json(["memory", "pending", "--diff", "--json"])
    stored = next(item for item in diffed["memories"] if item["code"] == new["code"])
    changes = {item["field"]: item for item in stored["diff"]}
    h.ok(
        changes["content"]["before"] == "contenido viejo"
        and changes["content"]["after"] == "contenido nuevo"
        and "title" in changes,
        "pending --diff muestra antes, después y título",
    )

    human_code, human, _ = _run(["memory", "pending", "--diff"])
    h.ok(
        human_code == 0
        and "- content: contenido viejo" in human
        and "+ content: contenido nuevo" in human,
        "pending --diff imprime el cambio resumido",
    )

    fresh = add_memory(
        h.conn,
        title="Propuesta sin supersesión",
        content="sin relación previa",
        key="sin-supersesion",
        source="agent",
        actor="worker",
        evidence=[evidence["code"]],
    )
    _, diffed = _json(["memory", "pending", "--diff", "--json"])
    stored = next(item for item in diffed["memories"] if item["code"] == fresh["code"])
    code, human, _ = _run(["memory", "pending", "--diff"])
    h.ok(
        stored["diff"] == []
        and code == 0
        and "Sin supersesión: propuesta nueva." in human,
        "una propuesta nueva no tiene diff en JSON ni en texto",
    )


def run(h: Harness) -> None:
    _candidates(h)
    _review(h)
    _pending_diff(h)
