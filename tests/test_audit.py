"""Auditoría de cierres: ``unresolved`` y documentación sin reabrir."""

from __future__ import annotations

from ticketlib import audit as audit_lib, repo
from tests.support import Harness


def run(h: Harness) -> None:
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
