"""Auditoría de cierres: ``unresolved``, rechazos de entrega y evidencia."""

from __future__ import annotations

from typing import Any

from ticketlib import audit as audit_lib, repo
from ticketlib.domain import ValidationError
from tests.support import Harness


def _existing(h: Harness) -> None:
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
    h.ok(
        before
        and documented["status"] == "confirmado"
        and documented["resolution"] == resolution
        and not after,
        "transition documenta un confirmado sin cambiar de estado y "
        "unresolved deja de listarlo",
    )

    # ── Sin commit enlazado: épicas fuera, hojas dentro ─────────────────
    epic = repo.create_ticket(conn, title="Épica confirmada sin commit")
    repo.create_ticket(conn, title="Hoja de la épica", parent_id=epic["code"])
    leaf = repo.create_ticket(conn, title="Hoja confirmada sin commit")
    linked = repo.create_ticket(conn, title="Hoja con commit enlazado")
    repo.link_ticket(conn, linked["code"], "commit", "abc1234def5678")
    conn.executemany(
        "UPDATE tickets SET status = 'confirmado' WHERE code = ?",
        [(epic["code"],), (leaf["code"],), (linked["code"],)],
    )
    conn.commit()

    pending = {item["code"] for item in audit_lib.uncommitted(conn)}
    excluded = {item["code"] for item in audit_lib.uncommitted_epics(conn)}
    h.ok(
        epic["code"] not in pending,
        "una épica confirmada no aparece como cerrada sin commit",
    )
    h.ok(
        leaf["code"] in pending,
        "una hoja confirmada sin commit sí aparece en el audit",
    )
    h.ok(
        linked["code"] not in pending,
        "una hoja con commit enlazado no aparece en el audit",
    )
    h.ok(
        epic["code"] in excluded and leaf["code"] not in excluded,
        "uncommitted_epics explica las épicas excluidas sin mezclar hojas",
    )


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


def _rejections(h: Harness) -> None:
    """Rechazos pre y post verificación, steps_exhausted y abandon."""
    from ticketlib import status
    from ticketlib.memory.stats import impact

    with Harness() as iso:
        conn = iso.conn
        # ── Rechazo pre-verificación sin cambio de estado ────────────────
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
        h.ok(
            rejected["status"] == "completado"
            and row is not None
            and row["actor"] == "conductor"
            and row["session_id"] == "ses_cond"
            and "ruff rojo" in row["note"],
            "reject_delivery deja evento delivery_rejected con motivo, actor "
            "y sesión sin cambiar de estado",
        )
        h.raises(
            ValidationError,
            lambda: repo.reject_delivery(conn, red["code"], "   "),
            "reject_delivery exige un motivo",
        )
        repo.reject_delivery(
            conn,
            red["code"],
            "ruff rojo: se reabre para rework",
            actor="conductor",
            session_id="ses_cond",
            reopen=True,
        )
        h.ok(
            repo.get_ticket(conn, red["code"])["status"] == "en_progreso",
            "reject_delivery con reopen=True reabre la entrega defectuosa",
        )

        # ── steps_exhausted + rechazo: UN fallo, no dos ───────────────────
        exhausted = _delivery(
            h, conn, "Agotado y luego rechazado", "ses_exh"
        )
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

        metrics = status.snapshot(with_servers=False, conn=conn)["metrics"]
        h.eq(
            metrics["verification_failures"],
            3,
            "los rechazos pre-verificación cuentan: dos del gate rojo y uno "
            "del agotado",
        )
        h.eq(
            metrics["steps_exhausted"],
            1,
            "un ticket agotado y rechazado no se duplica: solo el agotado "
            "puro queda como señal",
        )
        h.ok(
            metrics["steps_exhausted_by_profile"] == {"worker": 1},
            "la señal steps_exhausted se agrega por perfil",
        )

        # ── Rechazo post-verificación: suma al mismo contador ────────────
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

        # ── abandon no es un fallo ───────────────────────────────────────
        walked = repo.create_ticket(conn, title="Claim liberado")
        repo.claim_ticket(
            conn, walked["code"], agent="worker", session_id="ses_walk"
        )
        repo.abandon_ticket(
            conn, walked["code"], actor="worker", session_id="ses_walk"
        )

        metrics = status.snapshot(with_servers=False, conn=conn)["metrics"]
        h.eq(
            metrics["verification_failures"],
            4,
            "el rechazo post-verificación suma (3 pre + 1 post) y el abandon "
            "no infla el contador",
        )
        abandoned = repo.get_ticket(conn, walked["code"])
        h.ok(
            abandoned["status"] == "en_progreso"
            and int(abandoned["iterations"] or 0) <= 1,
            "abandon no toca estado ni intentos: no penaliza el first-pass",
        )

        # ── status._metrics y memory.stats coinciden ──────────────────────
        data = impact(conn, days=30)
        h.ok(
            data["verification"]["failures"]
            == metrics["verification_failures"]
            and data["verification"]["steps_exhausted"]
            == metrics["steps_exhausted"]
            and data["verification"]["steps_exhausted_by_profile"]
            == metrics["steps_exhausted_by_profile"],
            "impact y status._metrics cuentan los mismos fallos y la misma "
            "señal steps_exhausted",
        )
        bucket = next(
            (
                item
                for item in data["trend"]
                if item.get("steps_exhausted", 0) > 0
            ),
            None,
        )
        h.ok(
            bucket is not None and bucket["steps_exhausted"] == 1,
            "la tendencia cuenta steps_exhausted por periodo",
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
        h.ok(
            verified["status"] == "verificado"
            and note_row is not None
            and "revisión manual del conductor" in note_row["note"]
            and "gates: ruff ok · mypy ok" in note_row["note"]
            and "commits: 336312e" in note_row["note"]
            and "commands: python3 tests/run.py --only test_audit"
            in note_row["note"],
            "la evidencia estructurada se valida, se guarda y se renderiza "
            "legible junto al texto libre",
        )

        bare = _delivery(h, conn, "Verificado solo con evidencia", "ses_bare")
        repo.transition(
            conn,
            bare["code"],
            "verificado",
            actor="conductor",
            evidence={"gates": ["suite verde"]},
        )
        h.ok(
            repo.get_ticket(conn, bare["code"])["status"] == "verificado",
            "la evidencia estructurada basta sola para marcar verificado",
        )

        target = _delivery(h, conn, "Objetivo de evidencia inválida", "ses_bad")
        attempts = [
            ({"gates": "ruff"}, "un valor que no es lista"),
            ({"bogus": []}, "claves desconocidas"),
            ({"gates": ["x" * 201]}, "entradas de más de 200 caracteres"),
            ({"gates": [str(i) for i in range(21)]}, "más de 20 entradas"),
            ({"gates": [7]}, "entradas que no son cadenas"),
        ]
        for evidence, why in attempts:
            h.raises(
                ValidationError,
                lambda ev=evidence: repo.transition(
                    conn,
                    target["code"],
                    "verificado",
                    actor="conductor",
                    evidence=ev,
                ),
                f"evidence inválida se rechaza al escribir ({why})",
            )
        h.raises(
            ValidationError,
            lambda: repo.transition(
                conn,
                target["code"],
                "en_progreso",
                note="revisión",
                actor="conductor",
                evidence={"gates": ["suite verde"]},
            ),
            "la evidencia estructurada solo se admite al marcar verificado",
        )
        h.ok(
            repo.get_ticket(conn, target["code"])["status"] == "completado",
            "los intentos con evidencia inválida no cambian el estado",
        )


def run(h: Harness) -> None:
    _existing(h)
    _rejections(h)
    _evidence(h)
