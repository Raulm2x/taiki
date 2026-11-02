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
            "el paquete expone el contrato completo",
        )
        h.ok(bool(packet["generated_at"]), "el paquete sella la generación")

        current = packet["current_memories"]
        h.ok(
            any(item["code"] == memory["code"] for item in current),
            "la memoria propuesta aparece en el paquete",
        )
        h.ok(
            all("content" not in item for item in current),
            "las memorias se incluyen compactas",
        )
        h.ok(
            all(item["status"] in ("active", "proposed", "stale") for item in current),
            "solo se incluye memoria vigente o propuesta",
        )

        thresholds = packet["thresholds"]
        h.eq(thresholds["candidate_max"], 5, "el paquete refleja el umbral efectivo")
        h.eq(thresholds["candidate_min_evidence"], 2, "evidencia mínima visible")
        h.eq(thresholds["candidate_similarity"], 0.8, "similitud visible")
        h.eq(len(packet["rules"]), 7, "las reglas se listan completas")
        h.eq(len(packet["instructions"]), 7, "las instrucciones anti-aprendizaje van completas")

        recent = packet["recent_tickets"]
        h.eq(len(recent), 2, "solo entran tickets cerrados o reabiertos")
        by_code = {item["code"]: item for item in recent}
        h.ok(completed["code"] in by_code, "el ticket cerrado aparece")
        h.eq(
            by_code[completed["code"]]["resolution"],
            "hecho",
            "la resolución se conserva",
        )
        h.ok(rejected["code"] in by_code, "el ticket rechazado aparece")
        h.eq(
            by_code[rejected["code"]]["iterations"],
            2,
            "el rechazo cuenta los intentos",
        )
        h.ok(
            "[redactado]" in by_code[rejected["code"]]["rejection"],
            "el motivo del rechazo se redacta",
        )

        text = json.dumps(packet)
        h.ok("hunter2" not in text, "el paquete no filtra el secreto")
        h.ok("password:" not in text, "el paquete no filtra la credencial")
        h.ok("sk-" not in text, "el paquete no filtra claves")
        h.ok(
            all("body" not in item for item in recent),
            "el paquete no incluye cuerpos de comentarios",
        )

        one = review_packet(conn, limit=1)
        h.eq(len(one["recent_tickets"]), 1, "limit acota los tickets recientes")
        h.eq(
            [item["id"] for item in packet["candidates"]],
            [item["id"] for item in review_packet(conn, {"candidate_max": 5}, limit=3)["candidates"]],
            "los candidatos del paquete son deterministas",
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
