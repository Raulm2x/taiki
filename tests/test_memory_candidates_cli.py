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
    h.eq(code, 0, "memory candidates sale con 0")
    h.eq(set(payload), {"count", "candidates"}, "el JSON de candidatos es estable")
    h.eq(payload["count"], len(payload["candidates"]), "el count coincide")
    rework_candidate = next(
        item for item in payload["candidates"] if item["rule"] == "rework"
    )
    h.eq(
        rework_candidate["kind_sugerido"],
        "pitfall",
        "el candidato expone el tipo sugerido",
    )
    h.ok(
        any(
            item["kind"] == "ticket" and item["ref"] == rework["code"]
            for item in rework_candidate["evidence"]
        ),
        "el candidato incluye la evidencia del ticket",
    )
    h.ok(rework_candidate["sample_size"] >= 1, "el candidato declara la muestra")
    h.ok(0.0 <= rework_candidate["confidence"] <= 1.0, "la confianza está en rango")

    human_code, human, _ = _run(["memory", "candidates"])
    h.eq(human_code, 0, "la salida humana sale con 0")
    h.ok(
        "rework" in human and "Evidencia:" in human and "Muestra:" in human,
        "la salida humana muestra regla, evidencia y muestra",
    )

    _, pitfall = _json(["memory", "candidates", "--kind", "pitfall", "--json"])
    h.ok(pitfall["count"] >= 1, "--kind filtra candidatos del tipo pedido")
    h.ok(
        all(item["kind_sugerido"] == "pitfall" for item in pitfall["candidates"]),
        "--kind no deja pasar otros tipos",
    )
    _, limited = _json(["memory", "candidates", "--limit", "1", "--json"])
    h.eq(limited["count"], 1, "--limit acota la lista")
    _, combined = _json(
        ["memory", "candidates", "--kind", "pitfall", "--limit", "1", "--json"]
    )
    h.eq(combined["count"], 1, "--kind se aplica antes que --limit")
    bad_limit, _, err = _run(["memory", "candidates", "--limit", "0"])
    h.ok(bad_limit == 1 and "--limit" in err, "--limit inválido da error claro")
    _, windowed = _json(["memory", "candidates", "--since", "30d", "--json"])
    h.ok(windowed["count"] >= 1, "--since acepta una ventana en días")
    invalid, _, err = _run(["memory", "candidates", "--since", "0d"])
    h.ok(invalid == 1 and "--since" in err, "--since inválido da error claro")

    for index in range(21):
        _iterated(h.conn, f"Ciclo {index}")
    _, default = _json(["memory", "candidates", "--json"])
    _, everything = _json(["memory", "candidates", "--all", "--json"])
    h.eq(default["count"], 20, "sin --all se respeta candidate_max")
    h.ok(everything["count"] > default["count"], "--all ignora candidate_max")


def _review(h: Harness) -> None:
    code, packet = _json(["memory", "review", "--json"])
    h.eq(code, 0, "memory review sale con 0")
    h.eq(
        set(packet),
        {
            "generated_at",
            "candidates",
            "current_memories",
            "recent_tickets",
            "thresholds",
            "rules",
            "instructions",
        },
        "el paquete de revisión conserva su contrato",
    )
    h.ok(packet["candidates"], "el paquete incluye candidatos")
    h.ok(packet["instructions"], "el paquete incluye instrucciones")

    human_code, human, _ = _run(["memory", "review"])
    h.eq(human_code, 0, "la revisión humana sale con 0")
    h.ok(
        "Revisión de memoria" in human and "Instrucciones:" in human,
        "la revisión humana resume el paquete",
    )

    from commands import memory as memory_command

    original = memory_command.workspace.project_root
    memory_command.workspace.project_root = lambda: h.tmp
    try:
        write_code, write_out, _ = _run(["memory", "review", "--write"])
        artifact = Path(h.tmp) / ".taiki" / "generated" / "memory-review.json"
        h.ok(
            write_code == 0
            and artifact.is_file()
            and "Escrito:" in write_out,
            "review --write crea el artefacto y lo anuncia",
        )
        stored = json.loads(artifact.read_text(encoding="utf-8"))
        h.eq(
            set(stored),
            set(packet),
            "el artefacto guarda el contrato del paquete",
        )
        h.ok(bool(stored["generated_at"]), "el artefacto sella la generación")
        leftovers = list((Path(h.tmp) / ".taiki" / "generated").glob(".memory-review-*"))
        h.eq(leftovers, [], "review --write no deja temporales")

        _, written = _json(["memory", "review", "--write", "--json"])
        h.eq(written["path"], str(artifact), "review --json --write anuncia la ruta")
        h.ok(
            artifact.is_file() and "path" not in stored,
            "el artefacto guarda el paquete sin metadatos del CLI",
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
    h.eq(new["supersedes_id"], old["id"], "la nueva memoria enlaza a la anterior")
    h.eq(new["status"], "proposed", "la propuesta nace pendiente")

    _, plain = _json(["memory", "pending", "--json"])
    stored = next(item for item in plain["memories"] if item["code"] == new["code"])
    h.ok("diff" not in stored, "pending sin --diff no añade ruido")

    _, diffed = _json(["memory", "pending", "--diff", "--json"])
    stored = next(item for item in diffed["memories"] if item["code"] == new["code"])
    changes = {item["field"]: item for item in stored["diff"]}
    h.eq(changes["content"]["before"], "contenido viejo", "el diff muestra el antes")
    h.eq(changes["content"]["after"], "contenido nuevo", "el diff muestra el después")
    h.ok("title" in changes, "el diff incluye el título")

    human_code, human, _ = _run(["memory", "pending", "--diff"])
    h.eq(human_code, 0, "pending --diff humano sale con 0")
    h.ok(
        "- content: contenido viejo" in human
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
    h.eq(stored["diff"], [], "una propuesta nueva no tiene diff")

    code, human, _ = _run(["memory", "pending", "--diff"])
    h.ok(
        code == 0 and "Sin supersesión: propuesta nueva." in human,
        "el diff humano distingue una propuesta nueva",
    )


def run(h: Harness) -> None:
    _candidates(h)
    _review(h)
    _pending_diff(h)
