"""Paquete de revisión: estructura, contexto compacto y redacción de secretos."""

from __future__ import annotations

import json

from ticketlib import db, repo
from ticketlib.domain import ValidationError
from ticketlib.memory import add_memory, review_packet
from tests.support import Harness


def _ticket(conn, title, **kwargs):
    return repo.create_ticket(conn, title=title, **kwargs)


def _completed(conn, title, resolution="hecho"):
    ticket = _ticket(conn, title, kind="bug", paths=["ticketlib/memory/a.py"])
    repo.transition(
        conn, ticket["code"], "en_progreso", actor="worker", actor_type="agente"
    )
    repo.complete_ticket(
        conn,
        ticket["code"],
        resolution=resolution,
        actor="worker",
        actor_type="agente",
    )
    return ticket


def _rejected(conn, title, reason):
    ticket = _completed(conn, title, resolution="primera entrega")
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
        note=reason,
        actor="usuario",
        actor_type="usuario",
    )
    repo.comment_ticket(
        conn,
        ticket["code"],
        f"Rechazado en revisión: {reason}",
        author="usuario",
        actor_type="usuario",
    )
    return ticket


def _packet(h: Harness) -> None:
    conn = db.connect(h.tmp / "review.db")
    try:
        completed = _completed(conn, "Ticket cerrado")
        memory = add_memory(
            conn,
            title="Regla segura",
            content="Los tests usan una DB temporal por módulo.",
            key="regla-segura",
            source="agent",
            paths=["ticketlib/memory/review.py"],
            evidence=[completed["code"]],
        )
        rejected = _rejected(conn, "Ticket rechazado", "password: hunter2")
        for index in range(3):
            _ticket(conn, f"Antiguo {index}")

        packet = review_packet(conn, {"candidate_max": 5}, limit=3)
        h.ok(
            set(packet)
            == {
                "generated_at",
                "candidates",
                "current_memories",
                "recent_tickets",
                "thresholds",
                "rules",
                "instructions",
            }
            and bool(packet["generated_at"]),
            "el paquete expone el contrato completo y sella la generación",
        )

        current = packet["current_memories"]
        h.ok(
            any(item["code"] == memory["code"] for item in current)
            and all("content" not in item for item in current)
            and all(
                item["status"] in ("active", "proposed", "stale")
                for item in current
            ),
            "las memorias vigentes aparecen compactas y sin contenido",
        )

        thresholds = packet["thresholds"]
        h.ok(
            thresholds["candidate_max"] == 5
            and thresholds["candidate_min_evidence"] == 2
            and thresholds["candidate_similarity"] == 0.8,
            "el paquete refleja los umbrales efectivos",
        )
        h.ok(
            len(packet["rules"]) == 7 and len(packet["instructions"]) == 7,
            "las reglas e instrucciones anti-aprendizaje van completas",
        )

        recent = packet["recent_tickets"]
        by_code = {item["code"]: item for item in recent}
        h.eq(len(recent), 2, "solo entran tickets cerrados o reabiertos")
        h.ok(
            completed["code"] in by_code
            and by_code[completed["code"]]["resolution"] == "hecho",
            "el ticket cerrado aparece con su resolución",
        )
        h.ok(
            rejected["code"] in by_code
            and by_code[rejected["code"]]["iterations"] == 2,
            "el rechazo aparece y cuenta los intentos",
        )
        h.ok(
            "[redactado]" in by_code[rejected["code"]]["rejection"],
            "el motivo del rechazo se redacta",
        )

        text = json.dumps(packet)
        h.ok(
            "hunter2" not in text
            and "password:" not in text
            and "sk-" not in text,
            "el paquete no filtra secretos ni credenciales",
        )
        h.ok(
            all("body" not in item for item in recent),
            "el paquete no incluye cuerpos de comentarios",
        )

        one = review_packet(conn, limit=1)
        h.ok(
            len(one["recent_tickets"]) == 1
            and [item["id"] for item in packet["candidates"]]
            == [
                item["id"]
                for item in review_packet(
                    conn, {"candidate_max": 5}, limit=3
                )["candidates"]
            ],
            "limit acota los tickets recientes y los candidatos son deterministas",
        )
        h.raises(
            ValidationError,
            lambda: review_packet(conn, limit=0),
            "el límite de tickets recientes falla",
        )
    finally:
        conn.close()


def run(h: Harness) -> None:
    _packet(h)
