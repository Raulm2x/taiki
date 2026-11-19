"""Auditoría de cierres: ``unresolved``, rechazos de entrega y evidencia."""

from __future__ import annotations

from typing import Any

from ticketlib import audit as audit_lib, repo
from ticketlib.domain import ValidationError
from tests.support import Harness


def _delivery(h: Harness, conn: Any, title: str, session: str) -> Any:
    """Ticket entregado (``completado``) listo para revisar."""
    ticket = repo.create_ticket(conn, title=title)
    repo.claim_ticket(
        conn, ticket["code"], agent="worker", session_id=session
    )
    repo.complete_ticket(
        conn,
        ticket["code"],
        resolution="entrega",
        actor="worker",
        session_id=session,
    )
    return repo.get_ticket(conn, ticket["code"])


def _rejected(call: Any) -> bool:
    """True si ``call`` lanza ``ValidationError`` (tabla de aserciones)."""
    try:
        call()
    except ValidationError:
        return True
    return False


def _existing(h: Harness) -> None:
    """``unresolved`` documenta sin reabrir y ``uncommitted`` separa capas."""
    conn = h.conn
    epic = repo.create_ticket(conn, title="Épica confirmada sin resolución")
    conn.execute(
        "UPDATE tickets SET status = 'confirmado' WHERE code = ?",
        (epic["code"],),
    )
    conn.commit()
    before = any(
        item["code"] == epic["code"] for item in audit_lib.unresolved(conn)
    )
    resolution = "Cierre documentado después de la confirmación."
    documented = repo.transition(
        conn,
        epic["code"],
        "confirmado",
        resolution=resolution,
    )
    after = any(
        item["code"] == epic["code"] for item in audit_lib.unresolved(conn)
    )

    # ── Sin commit enlazado: épicas fuera, hojas dentro ─────────────────
    childful = repo.create_ticket(conn, title="Épica confirmada sin commit")
    repo.create_ticket(
        conn, title="Hoja de la épica", parent_id=childful["code"]
    )
    leaf = repo.create_ticket(conn, title="Hoja confirmada sin commit")
    linked = repo.create_ticket(conn, title="Hoja con commit enlazado")
    repo.link_ticket(conn, linked["code"], "commit", "abc1234def5678")
    conn.executemany(
        "UPDATE tickets SET status = 'confirmado' WHERE code = ?",
        [(childful["code"],), (leaf["code"],), (linked["code"],)],
    )
    conn.commit()
    pending = {item["code"] for item in audit_lib.uncommitted(conn)}
    excluded = {item["code"] for item in audit_lib.uncommitted_epics(conn)}

    h.eq(
        (
            before,
            documented["status"],
            documented["resolution"],
            after,
            childful["code"] in pending,
            leaf["code"] in pending,
            linked["code"] in pending,
            childful["code"] in excluded,
            leaf["code"] in excluded,
        ),
        (
            True,
            "confirmado",
            resolution,
            False,
            False,
            True,
            False,
            True,
            False,
        ),
        "transition documenta un confirmado sin reabrir (unresolved lo "
        "retira) y uncommitted separa las épicas de las hojas",
    )


def _rejections(h: Harness) -> None:
    """Rechazos pre/post, steps_exhausted sin doble conteo y abandon."""
    from ticketlib import status
    from ticketlib.memory.stats import impact

    with Harness() as iso:
        conn = iso.conn
        # ── Rechazo pre-verificación sin cambio de estado (+ reopen) ─────
        red = _delivery(h, conn, "Gate rojo antes de verificar", "ses_red")
        rejected = repo.reject_delivery(
            conn,
            red["code"],
            "ruff rojo en ticketlib/status.py",
            actor="conductor",
            session_id="ses_cond",
        )
        row = conn.execute(
            "SELECT actor, session_id, note FROM events "
            "WHERE ticket_id = ? AND action = 'delivery_rejected'",
            (red["id"],),
        ).fetchone()
        empty_reason = _rejected(
            lambda: repo.reject_delivery(conn, red["code"], "   ")
        )
        repo.reject_delivery(
            conn,
            red["code"],
            "ruff rojo: se reabre para rework",
            actor="conductor",
            session_id="ses_cond",
            reopen=True,
        )
        red_reopened = repo.get_ticket(conn, red["code"])["status"]

        # ── steps_exhausted + rechazo: UN fallo, no dos ───────────────────
        exhausted = _delivery(h, conn, "Agotado y luego rechazado", "ses_exh")
        repo.steps_exhausted(
            conn,
            exhausted["code"],
            phase="completado",
            actor="worker",
            session_id="ses_exh",
        )
        repo.reject_delivery(
            conn,
            exhausted["code"],
            "entrega a medias",
            actor="conductor",
            session_id="ses_cond",
        )
        only = _delivery(h, conn, "Solo pasos agotados", "ses_only")
        repo.steps_exhausted(
            conn,
            only["code"],
            phase="completado",
            actor="worker",
            session_id="ses_only",
        )
        pre = status.snapshot(with_servers=False, conn=conn)["metrics"]

        # ── Rechazo post-verificación y abandon ──────────────────────────
        verified = _delivery(h, conn, "Verificado y reabierto", "ses_ver")
        repo.transition(
            conn,
            verified["code"],
            "verificado",
            note="gates verdes",
            actor="conductor",
        )
        repo.transition(
            conn,
            verified["code"],
            "en_progreso",
            note="revisión: falta un caso",
            actor="conductor",
        )
        walked = repo.create_ticket(conn, title="Claim liberado")
        repo.claim_ticket(
            conn, walked["code"], agent="worker", session_id="ses_walk"
        )
        repo.abandon_ticket(
            conn, walked["code"], actor="worker", session_id="ses_walk"
        )
        post = status.snapshot(with_servers=False, conn=conn)["metrics"]
        abandoned = repo.get_ticket(conn, walked["code"])
        data = impact(conn, days=30)
        bucket = next(
            (
                item
                for item in data["trend"]
                if item.get("steps_exhausted", 0) > 0
            ),
            None,
        )

        note = row["note"] if row is not None else ""
        h.eq(
            (
                rejected["status"],
                row["actor"] if row is not None else None,
                row["session_id"] if row is not None else None,
                "ruff rojo" in note,
                empty_reason,
                red_reopened,
                pre["verification_failures"],
                pre["steps_exhausted"],
                pre["steps_exhausted_by_profile"],
                post["verification_failures"],
                abandoned["status"],
                int(abandoned["iterations"] or 0),
                data["verification"]["failures"],
                data["verification"]["steps_exhausted"],
                data["verification"]["steps_exhausted_by_profile"],
                bucket["steps_exhausted"] if bucket else 0,
            ),
            (
                "completado",
                "conductor",
                "ses_cond",
                True,
                True,
                "en_progreso",
                3,
                1,
                {"worker": 1},
                4,
                "en_progreso",
                1,
                4,
                1,
                {"worker": 1},
                1,
            ),
            "los rechazos pre (gate rojo x2, agotado) y post (verificado "
            "reabierto) suman 4, steps_exhausted no se duplica con el "
            "rechazo, abandon no toca estado ni intentos, e impact coincide "
            "con status._metrics por periodo y perfil",
        )


def _evidence(h: Harness) -> None:
    """Evidencia estructurada en ``verificado``: válida e inválida."""
    with Harness() as iso:
        conn = iso.conn
        ticket = _delivery(h, conn, "Verificado con evidencia", "ses_ev")
        verified = repo.transition(
            conn,
            ticket["code"],
            "verificado",
            note="revisión manual del conductor",
            actor="conductor",
            evidence={
                "gates": ["ruff ok", "mypy ok"],
                "commits": ["336312e"],
                "commands": ["python3 tests/run.py --only test_audit"],
            },
        )
        note_row = conn.execute(
            "SELECT note FROM events WHERE ticket_id = ? "
            "AND action = 'transicion' AND to_status = 'verificado'",
            (ticket["id"],),
        ).fetchone()
        note = note_row["note"] if note_row is not None else ""

        bare = _delivery(h, conn, "Verificado solo con evidencia", "ses_bare")
        repo.transition(
            conn,
            bare["code"],
            "verificado",
            actor="conductor",
            evidence={"gates": ["suite verde"]},
        )
        bare_status = repo.get_ticket(conn, bare["code"])["status"]

        target = _delivery(
            h, conn, "Objetivo de evidencia inválida", "ses_bad"
        )
        attempts = [
            lambda: repo.transition(
                conn,
                target["code"],
                "verificado",
                actor="conductor",
                evidence={"gates": "ruff"},
            ),
            lambda: repo.transition(
                conn,
                target["code"],
                "verificado",
                actor="conductor",
                evidence={"bogus": []},
            ),
            lambda: repo.transition(
                conn,
                target["code"],
                "verificado",
                actor="conductor",
                evidence={"gates": ["x" * 201]},
            ),
            lambda: repo.transition(
                conn,
                target["code"],
                "verificado",
                actor="conductor",
                evidence={"gates": [str(i) for i in range(21)]},
            ),
            lambda: repo.transition(
                conn,
                target["code"],
                "verificado",
                actor="conductor",
                evidence={"gates": [7]},
            ),
            lambda: repo.transition(
                conn,
                target["code"],
                "en_progreso",
                note="revisión",
                actor="conductor",
                evidence={"gates": ["suite verde"]},
            ),
        ]
        outcomes = [_rejected(call) for call in attempts]
        target_status = repo.get_ticket(conn, target["code"])["status"]

        h.eq(
            (
                verified["status"],
                "revisión manual del conductor" in note,
                "gates: ruff ok · mypy ok" in note,
                "commits: 336312e" in note,
                "commands: python3 tests/run.py --only test_audit" in note,
                bare_status,
                outcomes,
                target_status,
            ),
            (
                "verificado",
                True,
                True,
                True,
                True,
                "verificado",
                [True] * 6,
                "completado",
            ),
            "la evidencia estructurada se guarda y renderiza legible junto "
            "al texto libre (y basta sola); la inválida (valor no lista, "
            "claves desconocidas, >200 chars, >20 entradas, no cadena, "
            "estado que no es verificado) se rechaza al escribir sin "
            "cambiar el estado",
        )


def run(h: Harness) -> None:
    _existing(h)
    _rejections(h)
    _evidence(h)
