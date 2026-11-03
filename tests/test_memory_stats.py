"""Estadísticas e impacto de memoria: agregados estables y contrato CLI."""

from __future__ import annotations

import contextlib
import io
import json
from datetime import datetime, timedelta

import cli
from ticketlib import repo
from ticketlib.domain import ValidationError
from ticketlib.memory import add_memory, feedback_memory, get_memory
from ticketlib.memory.config import resolve_memory_config, validate_memory_config
from ticketlib.memory.stats import impact, stats
from tests.support import Harness


def _run(argv: list[str]) -> tuple[int, str, str]:
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        try:
            code = cli.main(argv)
        except SystemExit as exc:
            code = int(exc.code or 0)
    return code, out.getvalue(), err.getvalue()


def _json(argv: list[str]) -> dict:
    code, out, err = _run(argv)
    if code != 0:
        raise AssertionError(f"CLI falló ({code}): {err}")
    return json.loads(out)


def _stamp(days: int, hours: int = 0) -> str:
    return (
        datetime.now().astimezone() - timedelta(days=days, hours=hours)
    ).isoformat(timespec="seconds")


def _stats(h: Harness) -> None:
    conn = h.conn
    active = add_memory(
        conn,
        title="Activa de stats",
        content="Contenido activo.",
        kind="convention",
        key="stats-active",
        source="user",
    )
    proposal = add_memory(
        conn,
        title="Propuesta de stats",
        content="Pendiente de decisión.",
        kind="decision",
        key="stats-proposal",
        source="agent",
        evidence=[{"kind": "ticket", "ref": "T-0048.02"}],
    )
    stale = add_memory(
        conn,
        title="Stale de stats",
        content="Caducada.",
        key="stats-stale",
        source="user",
    )
    archived = add_memory(
        conn,
        title="Archivada de stats",
        content="Archivada.",
        key="stats-archived",
        source="user",
    )
    conn.execute("UPDATE memories SET status = 'stale' WHERE code = ?", (stale["code"],))
    conn.execute(
        "UPDATE memories SET status = 'archived', archived_at = ? WHERE code = ?",
        (_stamp(1), archived["code"]),
    )
    get_memory(conn, active["code"], session_id="ses_stats", record_usage=True)
    feedback_memory(conn, active["code"], helpful=True, note="útil")

    first = repo.create_ticket(conn, title="Routing A", kind="bug")
    second = repo.create_ticket(conn, title="Routing B", kind="bug")
    for ticket in (first, second):
        repo.claim_ticket(
            conn, ticket["code"], agent="worker-a", session_id="ses_route"
        )
        repo.complete_ticket(
            conn,
            ticket["code"],
            resolution="ok",
            actor="worker-a",
            session_id="ses_route",
        )
    conn.execute(
        "UPDATE tickets SET iterations = 2 WHERE code = ?", (second["code"],)
    )
    conn.commit()

    data = stats(conn)
    h.ok(
        set(data)
        == {
            "generated_at",
            "total",
            "by_status",
            "by_scope",
            "by_kind",
            "budget",
            "proposals",
            "usage",
            "routing",
        }
        and data["total"] >= 4
        and data["by_status"]["active"] >= 1
        and data["by_status"]["proposed"] == 1
        and data["by_status"]["stale"] == 1
        and data["by_status"]["archived"] == 1,
        "stats devuelve un contrato estable y cuenta por estado",
    )
    h.ok(
        data["by_scope"]["project"] >= 4
        and data["by_kind"]["convention"] >= 1
        and {"project", "user"} <= set(data["budget"])
        and data["budget"]["project"]["limit"] > 0,
        "stats agrupa por scope/kind e incluye presupuesto por scope",
    )
    h.ok(
        data["proposals"]["codes"] == [proposal["code"]]
        and data["usage"]["events"] >= 2
        and data["usage"]["use_count"] >= 1
        and data["usage"]["helpful"] >= 1,
        "stats expone propuestas y agrega uso y feedback",
    )
    route = next(
        item
        for item in data["routing"]["groups"]
        if item["profile"] == "worker-a"
    )
    h.ok(
        route["samples"] == 2
        and route["first_pass"] == 1
        and route["avg_attempts"] is not None,
        "stats resume routing observado",
    )

    payload = _json(["memory", "stats", "--json"])
    code, human, _ = _run(["memory", "stats"])
    h.ok(
        set(payload) == set(data)
        and code == 0
        and "Memorias:" in human
        and "Presupuesto:" in human,
        "el CLI de stats conserva el contrato JSON y la salida humana",
    )

    conn.execute(
        "UPDATE tickets SET archived = 1 WHERE code IN (?, ?)",
        (first["code"], second["code"]),
    )
    conn.commit()

    h.raises(
        ValidationError,
        lambda: validate_memory_config({"bogus": 1}),
        "el bloque memory sigue rechazando claves desconocidas",
    )
    h.raises(
        ValidationError,
        lambda: resolve_memory_config({"project": "x", "memory": {"bogus": 1}}),
        "el config raíz con una clave memory desconocida también falla",
    )


def _impact(h: Harness) -> None:
    conn = h.conn
    first = repo.create_ticket(conn, title="Impacto antiguo", kind="bug")
    second = repo.create_ticket(conn, title="Impacto reciente", kind="funcionalidad")
    conn.execute(
        "UPDATE tickets SET status = 'completado', iterations = 1, "
        "started_at = ?, completed_at = ?, updated_at = ? WHERE id = ?",
        (_stamp(20, hours=2), _stamp(20), _stamp(20), first["id"]),
    )
    conn.execute(
        "UPDATE tickets SET status = 'completado', iterations = 2, "
        "started_at = ?, completed_at = ?, updated_at = ? WHERE id = ?",
        (_stamp(3, hours=5), _stamp(3), _stamp(3), second["id"]),
    )
    conn.execute(
        "INSERT INTO events(ticket_id, ts, actor, actor_type, action, "
        "from_status, to_status) VALUES (?, ?, 'conductor', 'agente', "
        "'transicion', 'completado', 'verificado')",
        (first["id"], _stamp(20)),
    )
    conn.execute(
        "INSERT INTO events(ticket_id, ts, actor, actor_type, action, "
        "from_status, to_status) VALUES (?, ?, 'conductor', 'agente', "
        "'transicion', 'verificado', 'en_progreso')",
        (second["id"], _stamp(3)),
    )
    conn.commit()

    data = impact(conn, days=30)
    h.ok(
        set(data)
        == {
            "generated_at",
            "days",
            "cutoff",
            "closed",
            "attempts",
            "lead_time_hours",
            "verification",
            "trend",
            "notes",
        }
        and data["closed"] == 2
        and data["attempts"]["total"] == 3
        and data["attempts"]["avg"] == 1.5
        and data["lead_time_hours"]["avg"] == 3.5,
        "impact devuelve contrato, cierres, intentos y lead time",
    )
    h.ok(
        data["verification"]["verified"] == 1
        and data["verification"]["failures"] == 1
        and len(data["trend"]) == 2
        and data["notes"]
        and "causalidad" in data["notes"][0],
        "impact cuenta verificaciones, agrupa la tendencia y no afirma causalidad",
    )

    recent = impact(conn, days=1)
    h.eq(recent["closed"], 0, "la ventana de impact acota los cierres")

    payload = _json(["memory", "impact", "--days", "30", "--json"])
    h.ok(
        set(payload) == set(data) and payload["days"] == 30,
        "el CLI de impact conserva el contrato y pasa --days",
    )
    code, human, _ = _run(["memory", "impact", "--days", "30"])
    h.ok(
        code == 0 and "Impacto" in human and "Nota:" in human,
        "la salida humana de impact resume la tendencia",
    )
    bad_code, _, err = _run(["memory", "impact", "--days", "0"])
    h.ok(
        bad_code == 1 and "días de impacto" in err,
        "--days inválido da un error claro",
    )


def run(h: Harness) -> None:
    _stats(h)
    _impact(h)
