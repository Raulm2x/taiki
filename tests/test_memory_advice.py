"""Routing observado y checklist: muestras, Wilson y filtros por tipo/rutas."""

from __future__ import annotations

from ticketlib import repo
from ticketlib.domain import ValidationError
from ticketlib.memory import add_memory, archive_memory
from ticketlib.memory.advice import checklist, routing_advice
from ticketlib.memory.candidates import wilson_lower
from tests.support import Harness


def _closed(
    conn,
    *,
    kind: str,
    profile: str,
    iterations: int = 0,
    paths: list[str] | None = None,
) -> dict:
    ticket = repo.create_ticket(
        conn,
        title=f"Cerrado {profile} {iterations}",
        kind=kind,
        paths=paths or [],
        project="advice",
    )
    conn.execute(
        "UPDATE tickets SET status = 'confirmado', assigned_to = ?, iterations = ? "
        "WHERE id = ?",
        (profile, iterations, ticket["id"]),
    )
    return ticket


def _routing(h: Harness) -> None:
    conn = h.conn
    for _ in range(4):
        _closed(conn, kind="bug", profile="worker-high", paths=["src/a/"])
    h.eq(
        routing_advice(conn, "bug", ["src/a/x.py"]),
        None,
        "con menos de 5 muestras no hay advice",
    )
    _closed(
        conn,
        kind="bug",
        profile="worker-high",
        iterations=2,
        paths=["src/a/"],
    )
    advice = routing_advice(conn, "bug", ["src/a/x.py"])
    assert advice is not None
    h.ok(
        advice["profile"] == "worker-high"
        and advice["sample"] == 5
        and advice["first_pass"] == 0.8
        and advice["avg_attempts"] == 0.4,
        "con 5 muestras calcula perfil, first-pass e intentos medios",
    )
    h.eq(
        advice["confidence"],
        round(wilson_lower(4, 5), 3),
        "la confianza es el límite inferior de Wilson",
    )
    h.ok(
        advice["variant"] == "high"
        and advice["steps"] == 40
        and "4/5" in advice["reason"],
        "el advice incluye variante, steps y motivo con la muestra",
    )

    for _ in range(5):
        _closed(conn, kind="bug", profile="worker-medium", paths=["docs/"])
    global_advice = routing_advice(conn, "bug")
    assert global_advice is not None
    h.eq(
        global_advice["profile"],
        "worker-medium",
        "sin rutas gana el perfil con mejor Wilson",
    )
    scoped = routing_advice(conn, "bug", ["src/a/x.py"])
    h.ok(
        scoped is not None and scoped["profile"] == "worker-high",
        "las rutas acotan el histórico al perfil relevante",
    )
    h.eq(
        routing_advice(conn, "bug", ["nada/"]),
        None,
        "sin muestras en las rutas no se extrapola de rutas ajenas",
    )
    h.eq(routing_advice(conn, None), None, "sin tipo no hay advice")
    h.raises(
        ValidationError,
        lambda: routing_advice(conn, "inventado"),
        "un tipo de ticket inválido se rechaza",
    )


def _checklist(h: Harness) -> None:
    conn = h.conn
    path_pitfall = add_memory(
        conn,
        title="Pitfall de advice",
        description="Revisa el Wilson y la muestra.",
        content="Comprueba que la muestra supera el mínimo.",
        kind="pitfall",
        paths=["ticketlib/memory/advice.py"],
        confidence=0.9,
        source="user",
        evidence=[{"kind": "ticket", "ref": "T-0047.04"}],
    )
    convention = add_memory(
        conn,
        title="Convención de checks",
        description="Un test por comportamiento.",
        content="No dupliques la misma comprobación.",
        kind="convention",
        paths=["ticketlib/memory/checks.py"],
        source="user",
    )
    unrelated = add_memory(
        conn,
        title="Pitfall de docs",
        content="Documentación y código no se mezclan.",
        kind="pitfall",
        paths=["docs/"],
        source="user",
    )
    proposed = add_memory(
        conn,
        title="Pitfall propuesto",
        content="Propuesta que no debe entrar.",
        kind="pitfall",
        paths=["ticketlib/memory/advice.py"],
        confidence=0.9,
        source="agent",
        evidence=[{"kind": "file", "ref": "ticketlib/memory/advice.py"}],
    )
    archived = add_memory(
        conn,
        title="Pitfall archivado",
        content="Archivado que no debe entrar.",
        kind="pitfall",
        paths=["ticketlib/memory/advice.py"],
        source="user",
    )
    archive_memory(conn, archived["code"], reason="obsoleto")

    by_path = checklist(conn, None, ["ticketlib/memory/advice.py"])
    h.eq(
        [item["code"] for item in by_path],
        [path_pitfall["code"]],
        "el checklist filtra por rutas y excluye propuestas y archivadas",
    )
    h.ok(
        {"code", "kind", "title", "summary", "paths", "evidence", "confidence"}
        <= set(by_path[0])
        and by_path[0]["evidence"][0]["ref"] == "T-0047.04"
        and "ruta" in by_path[0]["reason"],
        "cada entrada es compacta y lleva evidencia y motivo",
    )
    h.eq(
        [item["code"] for item in checklist(conn, "pitfall", ["ticketlib/memory/advice.py"])],
        [path_pitfall["code"]],
        "kind=pitfall filtra por tipo de memoria",
    )
    h.eq(
        checklist(conn, "convention", ["ticketlib/memory/advice.py"]),
        [],
        "una convención de otras rutas no entra",
    )
    h.eq(
        [item["code"] for item in checklist(conn, "convention", ["ticketlib/memory/checks.py"])],
        [convention["code"]],
        "kind=convention devuelve la convención que casa",
    )
    general = checklist(conn, None, None)
    h.ok(
        len(general) == 3
        and unrelated["code"] in {item["code"] for item in general}
        and proposed["code"] not in {item["code"] for item in general},
        "sin filtros lista pits y convenciones activos sin propuestas",
    )
    h.eq(len(checklist(conn, None, None, limit=1)), 1, "el límite recorta el checklist")
    h.raises(
        ValidationError,
        lambda: checklist(conn, None, None, limit=0),
        "un límite inválido se rechaza",
    )
    h.raises(
        ValidationError,
        lambda: checklist(conn, "bug"),
        "kind solo admite pitfall o convention",
    )
    h.eq(
        checklist(conn, None, None, {"enabled": False}),
        [],
        "la memoria desactivada no devuelve checklist",
    )


def run(h: Harness) -> None:
    _routing(h)
    _checklist(h)
