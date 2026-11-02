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
