"""Invariante de numeración: los scratch no consumen números T-XXXX."""

from __future__ import annotations

from ticketlib import audit, db, domain, repo, scratch
from ticketlib.store import mutations, queries
from ticketlib.util import normalize_code
from tests.support import Harness


def _actions(ticket: dict) -> list[str]:
    return [item["action"] for item in ticket["events"]]


def run(h: Harness) -> None:
    conn = h.conn

    # ── normalize_code acepta ambos espacios ─────────────────────────────
    h.eq(normalize_code("s-7"), "S-7", "normaliza el espacio S-")
    h.eq(normalize_code("s-0007"), "S-0007", "respeta el relleno de S-")
    h.eq(normalize_code("12"), 12, "un número suelto sigue siendo un id")
    h.raises(
        domain.ValidationError,
        lambda: normalize_code("X-1"),
        "un prefijo desconocido no es un código válido",
    )
    h.raises(
        domain.ValidationError,
        lambda: repo.create_ticket(conn, title="Espacio raro", code_prefix="X"),
        "solo existen los espacios T y S",
    )

    # ── Contexto: tres tickets visibles, secuencia intacta ───────────────
    codes = [
        repo.create_ticket(conn, title=f"Visible {index}")["code"]
        for index in range(3)
    ]
    h.eq(codes, ["T-0001", "T-0002", "T-0003"], "los tickets visibles son consecutivos")
    h.eq(queries.missing_codes(conn), [], "una secuencia recién creada no tiene huecos")

    # ── Un scratch usa S- y no toca el contador T- ───────────────────────
    before = db.meta_get(conn, domain.TICKET_COUNTER)
    temporal = scratch.create_or_reuse(
        conn, agent="opencode", session_id="ses_scratch_1", title="Temporal"
    )
    h.eq(temporal["code"], "S-0001", "el primer scratch usa S-0001")
    h.eq(
        db.meta_get(conn, domain.TICKET_COUNTER),
        before,
        "crear un scratch no consume números T-XXXX",
    )
    h.eq(
        db.meta_get(conn, domain.SCRATCH_COUNTER),
        "2",
        "el contador de scratch avanza por su cuenta",
    )
    h.ok(
        "S-0001" not in {item["code"] for item in repo.list_tickets(conn)},
        "el scratch sigue oculto de la tabla",
    )
    reused = scratch.create_or_reuse(
        conn, agent="opencode", session_id="ses_scratch_1"
    )
    h.eq(reused["code"], "S-0001", "la misma sesión reutiliza su scratch")

    # ── Promover: recibe T-, se desarchiva y deja evento ─────────────────
    promoted = scratch.promote(conn, "S-0001")
    h.eq(promoted["code"], "T-0004", "al promover recibe el siguiente T- real")
    h.ok(promoted["archived"] is False, "promover desarchiva el ticket")
    h.ok(domain.SCRATCH_TAG not in promoted["tags"], "promover quita la marca temporal")
    h.ok("promover" in _actions(promoted), "promover deja evento en el historial")

    # ── Scratch antiguo con T-: conserva su número al promover ───────────
    legacy = repo.create_ticket(
        conn, title="Legacy scratch", tags=[domain.SCRATCH_TAG]
    )
    h.eq(legacy["code"], "T-0005", "el ticket antiguo ocupa un hueco ya consumido")
    with db.tx(conn):
        conn.execute(
            "UPDATE tickets SET archived = 1 WHERE id = ?", (legacy["id"],)
        )
    kept = scratch.promote(conn, "T-0005")
    h.eq(kept["code"], "T-0005", "promover conserva un T- antiguo y libre")
    h.ok(kept["archived"] is False, "el T- antiguo vuelve a la tabla")

    # ── Archivado automático con evento ──────────────────────────────────
    expired = scratch.create_or_reuse(
        conn, agent="opencode", session_id="ses_scratch_2"
    )
    with db.tx(conn):
        db.meta_set(
            conn, f"_scratch:{expired['code']}", "2000-01-01T00:00:00-05:00"
        )
    cleaned = scratch.clean(conn)
    h.ok(expired["code"] in cleaned, "clean archiva el scratch caducado")
    archived = repo.get_ticket(conn, expired["code"])
    h.ok(archived["archived"], "el scratch caducado queda archivado")
    h.ok(
        archived["status"] == "cancelado"
        and archived["owner_session"] is None
        and archived["lease_until"] is None,
        "el scratch archivado no deja un en_progreso fantasma",
    )
    h.ok("archivar" in _actions(archived), "archivar deja rastro en el historial")
    h.eq(
        [item["actor"] for item in archived["events"] if item["action"] == "archivar"],
        ["sistema"],
        "la caducidad la registra el sistema",
    )

    # ── Descartar a mano también deja evento ─────────────────────────────
    discarded = scratch.create_or_reuse(
        conn, agent="opencode", session_id="ses_scratch_3"
    )
    scratch.drop(conn, discarded["code"], actor="usuario")
    dropped = repo.get_ticket(conn, discarded["code"])
    h.ok(dropped["archived"], "drop archiva el scratch")
    h.ok(
        dropped["status"] == "cancelado" and dropped["owner_session"] is None,
        "drop cancela el scratch y libera su claim",
    )
    h.ok("archivar" in _actions(dropped), "drop deja rastro en el historial")

    # ── Hueco real vs código oculto ──────────────────────────────────────
    with db.tx(conn):
        conn.execute("UPDATE tickets SET archived = 1 WHERE id = ?", (kept["id"],))
    hidden = queries.hidden_codes(conn)
    h.ok(
        any(item["code"] == "T-0005" for item in hidden),
        "un T- archivado aparece como oculto",
    )
    h.ok(
        not any(item["code"].startswith("S-") for item in hidden),
        "los S- no cuentan como huecos",
    )
    h.eq(
        queries.missing_codes(conn),
        [],
        "un ticket archivado no es un hueco real: su fila existe",
    )

    # ── repair-gaps cubre los números que nunca existieron ───────────────
    with db.tx(conn):
        db.meta_set(conn, domain.TICKET_COUNTER, "9")
    gaps = queries.missing_codes(conn)
    h.eq(
        [item["code"] for item in gaps],
        ["T-0006", "T-0007", "T-0008"],
        "detecta los números sin fila",
    )
    plan = repo.fill_gaps(conn)
    h.eq(len(plan), 3, "la simulación no escribe nada")
    h.ok(
        queries.missing_codes(conn) == gaps,
        "sin --apply los huecos siguen ahí",
    )
    repo.fill_gaps(conn, apply=True, actor="usuario")
    h.eq(queries.missing_codes(conn), [], "tras aplicar no quedan huecos")
    tombstone = repo.get_ticket(conn, "T-0006")
    h.eq(tombstone["status"], "cancelado", "el hueco se cubre con un cancelado")
    h.ok(domain.TOMBSTONE_TAG in tombstone["tags"], "el ticket lleva la etiqueta hueco")
    h.ok(bool(tombstone["resolution"]), "el ticket explica por qué existe")
    h.ok(
        all(
            repo.get_ticket(conn, code)["archived"] is False
            for code in ("T-0006", "T-0007", "T-0008")
        ),
        "los tickets de hueco son visibles",
    )

    # ── El contador no retrocede y el código forzado se valida ───────────
    following = repo.create_ticket(conn, title="Siguiente visible")
    h.eq(following["code"], "T-0009", "el contador sigue donde estaba")
    h.raises(
        domain.ValidationError,
        lambda: repo.create_ticket(conn, title="Futuro", code="T-0010"),
        "no se puede forzar un código que aún no es un hueco",
    )
    h.raises(
        domain.ValidationError,
        lambda: repo.create_ticket(conn, title="Duplicado", code="T-0001"),
        "no se puede forzar un código existente",
    )

    # ── fill_gaps es atómico: un fallo a mitad no deja huecos sueltos ───
    with db.tx(conn):
        db.meta_set(conn, domain.TICKET_COUNTER, "13")
    pending = [item["code"] for item in queries.missing_codes(conn)]
    h.ok(pending, "hay huecos nuevos para probar la atomicidad")
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
        [item["code"] for item in queries.missing_codes(conn)],
        pending,
        "el fallo no deja huecos a medio cubrir",
    )
    with db.tx(conn):
        db.meta_set(conn, domain.TICKET_COUNTER, "10")

    # ── Auditoría expone ambas categorías ────────────────────────────────
    data = audit.audit(conn, repo=str(h.tmp))
    h.ok("hidden" in data, "audit incluye la lista de ocultos")
    h.eq(data["gaps"], [], "audit no confunde ocultos con huecos")
    h.ok(
        any(item["code"] == "T-0005" for item in data["hidden"]),
        "audit explica el código archivado",
    )
