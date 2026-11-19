"""Invariante de numeración: los scratch no consumen números T-XXXX."""

from __future__ import annotations

from ticketlib import audit, db, domain, repo, scratch
from ticketlib.store import mutations, queries
from ticketlib.util import normalize_code
from tests.support import Harness


def _actions(ticket: dict) -> list[str]:
    return [item["action"] for item in ticket["events"]]


def _rejected(call) -> bool:
    """True si ``call`` lanza ``ValidationError`` (tabla de aserciones)."""
    try:
        call()
    except domain.ValidationError:
        return True
    return False


def run(h: Harness) -> None:
    conn = h.conn

    # ── normalize_code acepta ambos espacios ─────────────────────────────
    h.eq(
        (
            normalize_code("s-7"),
            normalize_code("s-0007"),
            normalize_code("12"),
            _rejected(lambda: normalize_code("X-1")),
            _rejected(
                lambda: repo.create_ticket(
                    conn, title="Espacio raro", code_prefix="X"
                )
            ),
        ),
        ("S-7", "S-0007", 12, True, True),
        "normaliza el espacio S-, respeta su relleno, un número suelto es "
        "un id y solo existen los espacios T y S",
    )

    # ── Contexto: tres tickets visibles, secuencia intacta ───────────────
    codes = [
        repo.create_ticket(conn, title=f"Visible {index}")["code"]
        for index in range(3)
    ]
    h.eq(
        (codes, queries.missing_codes(conn)),
        (["T-0001", "T-0002", "T-0003"], []),
        "los tickets visibles son consecutivos y la secuencia no tiene huecos",
    )

    # ── Un scratch usa S- y no toca el contador T- ───────────────────────
    before = db.meta_get(conn, domain.TICKET_COUNTER)
    temporal = scratch.create_or_reuse(
        conn, agent="opencode", session_id="ses_scratch_1", title="Temporal"
    )
    reused = scratch.create_or_reuse(
        conn, agent="opencode", session_id="ses_scratch_1"
    )
    h.eq(
        (
            temporal["code"],
            db.meta_get(conn, domain.TICKET_COUNTER),
            db.meta_get(conn, domain.SCRATCH_COUNTER),
            "S-0001" in {item["code"] for item in repo.list_tickets(conn)},
            reused["code"],
        ),
        ("S-0001", before, "2", False, "S-0001"),
        "el primer scratch usa S-0001, no consume números T-, avanza su "
        "propio contador, queda oculto y la misma sesión lo reutiliza",
    )

    # ── Promover: recibe T-, se desarchiva y deja evento ─────────────────
    promoted = scratch.promote(conn, "S-0001")
    h.eq(
        (
            promoted["code"],
            promoted["archived"],
            domain.SCRATCH_TAG not in promoted["tags"],
            "promover" in _actions(promoted),
        ),
        ("T-0004", False, True, True),
        "al promover recibe el siguiente T- real, se desarchiva, pierde la "
        "marca temporal y deja evento en el historial",
    )

    # ── Scratch antiguo con T-: conserva su número al promover ───────────
    legacy = repo.create_ticket(
        conn, title="Legacy scratch", tags=[domain.SCRATCH_TAG]
    )
    with db.tx(conn):
        conn.execute(
            "UPDATE tickets SET archived = 1 WHERE id = ?", (legacy["id"],)
        )
    kept = scratch.promote(conn, "T-0005")
    h.eq(
        (legacy["code"], kept["code"], kept["archived"]),
        ("T-0005", "T-0005", False),
        "el ticket antiguo ocupa un hueco ya consumido y promover conserva "
        "un T- antiguo y libre, devolviéndolo a la tabla",
    )

    # ── Archivado automático con evento ──────────────────────────────────
    expired = scratch.create_or_reuse(
        conn, agent="opencode", session_id="ses_scratch_2"
    )
    with db.tx(conn):
        db.meta_set(
            conn, f"_scratch:{expired['code']}", "2000-01-01T00:00:00-05:00"
        )
    cleaned = scratch.clean(conn)
    archived = repo.get_ticket(conn, expired["code"])
    h.eq(
        (
            expired["code"] in cleaned,
            archived["archived"],
            archived["status"],
            archived["owner_session"],
            archived["lease_until"],
            "archivar" in _actions(archived),
            [
                item["actor"]
                for item in archived["events"]
                if item["action"] == "archivar"
            ],
        ),
        (True, True, "cancelado", None, None, True, ["sistema"]),
        "clean archiva el scratch caducado sin dejar un en_progreso "
        "fantasma, deja rastro en el historial y la registra el sistema",
    )

    # ── Descartar a mano también deja evento ─────────────────────────────
    discarded = scratch.create_or_reuse(
        conn, agent="opencode", session_id="ses_scratch_3"
    )
    scratch.drop(conn, discarded["code"], actor="usuario")
    dropped = repo.get_ticket(conn, discarded["code"])
    h.eq(
        (
            dropped["archived"],
            dropped["status"],
            dropped["owner_session"],
            "archivar" in _actions(dropped),
        ),
        (True, "cancelado", None, True),
        "drop archiva y cancela el scratch, libera su claim y deja rastro "
        "en el historial",
    )

    # ── Hueco real vs código oculto ──────────────────────────────────────
    with db.tx(conn):
        conn.execute("UPDATE tickets SET archived = 1 WHERE id = ?", (kept["id"],))
    hidden = queries.hidden_codes(conn)
    h.eq(
        (
            any(item["code"] == "T-0005" for item in hidden),
            not any(item["code"].startswith("S-") for item in hidden),
            queries.missing_codes(conn),
        ),
        (True, True, []),
        "un T- archivado aparece como oculto, los S- no cuentan como "
        "huecos y un ticket archivado no es un hueco real: su fila existe",
    )

    # ── repair-gaps cubre los números que nunca existieron ───────────────
    with db.tx(conn):
        db.meta_set(conn, domain.TICKET_COUNTER, "9")
    gaps = queries.missing_codes(conn)
    plan = repo.fill_gaps(conn)
    still_pending = [item["code"] for item in queries.missing_codes(conn)]
    repo.fill_gaps(conn, apply=True, actor="usuario")
    after_apply = queries.missing_codes(conn)
    tombstone = repo.get_ticket(conn, "T-0006")
    h.eq(
        (
            [item["code"] for item in gaps],
            len(plan),
            still_pending,
            [item["code"] for item in after_apply],
            tombstone["status"],
            domain.TOMBSTONE_TAG in tombstone["tags"],
            bool(tombstone["resolution"]),
            [
                repo.get_ticket(conn, code)["archived"]
                for code in ("T-0006", "T-0007", "T-0008")
            ],
        ),
        (
            ["T-0006", "T-0007", "T-0008"],
            3,
            ["T-0006", "T-0007", "T-0008"],
            [],
            "cancelado",
            True,
            True,
            [False, False, False],
        ),
        "detecta los números sin fila, la simulación no escribe nada, tras "
        "aplicar no quedan huecos y el cubrimiento es visible, cancelado y "
        "explicado",
    )

    # ── El contador no retrocede y el código forzado se valida ───────────
    following = repo.create_ticket(conn, title="Siguiente visible")
    h.eq(
        (
            following["code"],
            _rejected(
                lambda: repo.create_ticket(conn, title="Futuro", code="T-0010")
            ),
            _rejected(
                lambda: repo.create_ticket(
                    conn, title="Duplicado", code="T-0001"
                )
            ),
        ),
        ("T-0009", True, True),
        "el contador sigue donde estaba y no se puede forzar un código que "
        "aún no es un hueco ni uno existente",
    )

    # ── fill_gaps es atómico: un fallo a mitad no deja huecos sueltos ───
    with db.tx(conn):
        db.meta_set(conn, domain.TICKET_COUNTER, "13")
    pending = [item["code"] for item in queries.missing_codes(conn)]
    original_create = mutations.create_ticket
    calls = {"count": 0}

    def flaky_create(*args, **kwargs):
        calls["count"] += 1
        if calls["count"] == 2:
            raise RuntimeError("fallo simulado")
        return original_create(*args, **kwargs)

    mutations.create_ticket = flaky_create
    try:
        h.raises(
            RuntimeError,
            lambda: repo.fill_gaps(conn, apply=True, actor="usuario"),
            "un fallo a mitad aborta el cubrimiento de huecos",
        )
    finally:
        mutations.create_ticket = original_create
    h.eq(
        (bool(pending), [item["code"] for item in queries.missing_codes(conn)]),
        (True, pending),
        "el fallo no deja huecos a medio cubrir",
    )
    with db.tx(conn):
        db.meta_set(conn, domain.TICKET_COUNTER, "10")

    # ── Auditoría expone ambas categorías ────────────────────────────────
    data = audit.audit(conn, repo=str(h.tmp))
    h.eq(
        (
            "hidden" in data,
            data["gaps"],
            any(item["code"] == "T-0005" for item in data["hidden"]),
        ),
        (True, [], True),
        "audit incluye la lista de ocultos, no confunde ocultos con huecos "
        "y explica el código archivado",
    )
