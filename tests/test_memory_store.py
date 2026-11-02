"""Memoria semántica: esquema, CRUD, política, presupuesto y ledger."""

from __future__ import annotations

from ticketlib import db, repo
from ticketlib.domain import ValidationError
from ticketlib.memory import (
    BudgetError,
    add_memory,
    approve_memory,
    archive_memory,
    feedback_memory,
    get_memory,
    list_memories,
    patch_memory,
    pin_memory,
    reject_memory,
    restore_memory,
    search_memories,
    supersede_memory,
)
from ticketlib.memory import ledger as memory_ledger
from tests.support import Harness


def _migration(h: Harness) -> None:
    """Una DB v4 conserva tickets y comentarios al migrar a v5."""
    path = h.tmp / "v4.db"
    old = db.connect(path)
    try:
        ticket = repo.create_ticket(old, title="Sobrevive a la migración")
        repo.comment_ticket(old, ticket["code"], "comentario previo")
        for table in (
            "memories_fts",
            "memories",
            "memory_evidence",
            "memory_files",
            "memory_usage",
            "memory_ledger",
            "memory_blobs",
        ):
            old.execute(f"DROP TABLE IF EXISTS {table}")
        old.execute("PRAGMA user_version = 4")
        h.eq(db.schema_version(old), 4, "la DB de prueba arranca en v4")
        db.migrate(old)
        h.eq(db.schema_version(old), 5, "la migración sube a v5")
        h.eq(
            repo.get_ticket(old, ticket["code"])["title"],
            "Sobrevive a la migración",
            "el ticket se conserva tras migrar",
        )
        h.ok(
            old.execute(
                "SELECT 1 FROM comments WHERE ticket_id = ?", (ticket["id"],)
            ).fetchone()
            is not None,
            "el comentario se conserva tras migrar",
        )
        migrated = add_memory(
            old,
            title="Memoria migrada",
            content="El trigger FTS funciona después de migrar.",
            key="migrada",
            source="user",
        )
        h.eq(migrated["code"], "M-0001", "la DB migrada numera memorias")
        h.ok(
            old.execute(
                "SELECT 1 FROM memories_fts WHERE memories_fts MATCH 'trigger'"
            ).fetchone()
            is not None,
            "el FTS de memorias funciona tras la migración",
        )
    finally:
        old.close()


def _crud(h: Harness) -> None:
    conn = h.conn
    first = add_memory(
        conn,
        title="Rama de integración",
        content="El trabajo diario vive en develop y se integra con --ff-only.",
        kind="convention",
        key="rama-integracion",
        source="user",
        actor="tester",
    )
    h.eq(first["code"], "M-0001", "el primer código es M-0001")
    h.eq(first["status"], "active", "source=user nace activa")
    h.eq(first["source"], "user", "la procedencia se guarda")
    h.eq(first["budget"]["limit"], 3000, "el presupuesto por defecto es 3000")

    user_memory = add_memory(
        conn,
        title="Preferencia de commits",
        content="Prefiero commits pequeños y con código en el asunto.",
        kind="preference",
        scope="user",
        source="user",
    )
    h.eq(user_memory["scope"], "user", "se pueden crear memorias de usuario")
    h.eq(
        len(list_memories(conn, scope="user")),
        1,
        "list_memories filtra por scope",
    )
    h.eq(
        list_memories(conn, scope="project")[0]["code"],
        first["code"],
        "list_memories filtra el scope project",
    )

    h.ok(
        get_memory(conn, "M-1")["code"] == first["code"],
        "get_memory normaliza M-1 a M-0001",
    )
    h.raises(
        ValidationError,
        lambda: get_memory(conn, "M-9999"),
        "get_memory falla con una memoria inexistente",
    )

    found = search_memories(conn, "develop", record_usage=False)
    h.ok(
        any(item["code"] == first["code"] for item in found),
        "la búsqueda FTS encuentra por contenido",
    )
    accented = add_memory(
        conn,
        title="Configuración de ramas",
        content="Las ramas agent/* nacen de develop.",
        kind="convention",
        key="configuracion-ramas",
        source="user",
    )
    h.ok(
        any(
            item["code"] == accented["code"]
            for item in search_memories(conn, "configuracion", record_usage=False)
        ),
        "la búsqueda FTS ignora acentos",
    )
    usage = search_memories(conn, "ramas", session_id="ses_test")
    h.ok(usage, "search_memories devuelve resultados")
    h.ok(
        conn.execute(
            "SELECT 1 FROM memory_usage WHERE action = 'search' LIMIT 1"
        ).fetchone()
        is not None,
        "la búsqueda registra uso",
    )

    patched = patch_memory(
        conn,
        first["code"],
        description="Integración diaria.",
        actor="tester",
    )
    h.eq(patched["description"], "Integración diaria.", "patch_memory actualiza campos")
    h.raises(
        ValidationError,
        lambda: patch_memory(
            conn,
            first["code"],
            title=accented["title"],
            content=accented["content"],
        ),
        "patch_memory rechaza duplicados exactos",
    )


def _policy(h: Harness) -> None:
    conn = h.conn
    h.raises(
        ValidationError,
        lambda: add_memory(
            conn,
            title="Sin evidencia",
            content="No debería guardarse.",
            source="agent",
        ),
        "source=agent exige evidencia",
    )

    solid = add_memory(
        conn,
        title="Riesgo de FTS externo",
        content="Un DELETE sin trigger deja el índice FTS desincronizado.",
        kind="pitfall",
        key="fts-delete",
        source="agent",
        confidence=0.8,
        evidence=[
            {"kind": "ticket", "ref": "T-0045"},
            {"kind": "commit", "ref": "abc1234"},
        ],
    )
    h.eq(solid["status"], "active", "agent con 2 evidencias y confianza alta activa")
    h.eq(solid["ticket_id"], "T-0045", "la evidencia de ticket rellena ticket_id")

    weak = add_memory(
        conn,
        title="Detalle de FTS",
        content="El tokenizador unicode61 quita diacríticos.",
        kind="fact",
        key="fts-tokenizer",
        source="agent",
        confidence=0.9,
        evidence=[{"kind": "file", "ref": "ticketlib/db.py"}],
    )
    h.eq(weak["status"], "proposed", "agent con una evidencia queda propuesta")

    negative = add_memory(
        conn,
        title="Regla negativa",
        content="Nunca uses git push directo a main.",
        kind="convention",
        key="push-directo",
        source="agent",
        confidence=0.9,
        evidence=[
            {"kind": "ticket", "ref": "T-0045"},
            {"kind": "session", "ref": "ses_123"},
        ],
    )
    h.eq(negative["status"], "proposed", "una regla negativa siempre queda propuesta")

    derived = add_memory(
        conn,
        title="Derivada automática",
        content="El esquema v5 añade memoria.",
        kind="fact",
        key="esquema-v5",
        source="derived",
    )
    h.eq(derived["status"], "active", "source=derived nace activa")

    approved = approve_memory(conn, weak["code"], actor="usuario")
    h.eq(approved["status"], "active", "approve_memory activa una propuesta")
    h.raises(
        ValidationError,
        lambda: approve_memory(conn, solid["code"]),
        "approve_memory rechaza una memoria ya activa",
    )

    rejected = reject_memory(conn, negative["code"], actor="usuario")
    h.eq(rejected["status"], "archived", "reject_memory archiva la propuesta")
    h.ok(
        list_memories(conn, status="archived")[0]["code"] == negative["code"],
        "las archivadas se listan explícitamente",
    )

    pinned = pin_memory(conn, solid["code"])
    h.ok(pinned["pinned"], "pin_memory fija la memoria")
    archived = archive_memory(conn, solid["code"])
    h.eq(archived["status"], "archived", "archive_memory archiva")
    restored = restore_memory(conn, solid["code"])
    h.eq(restored["status"], "active", "restore_memory reactiva")
    unpinned = pin_memory(conn, solid["code"], pinned=False)
    h.ok(not unpinned["pinned"], "pin_memory puede desfijar")

    feedback = feedback_memory(conn, solid["code"], helpful=True, note="útil")
    h.eq(feedback["helpful_count"], 1, "feedback actualiza helpful_count")
    feedback = feedback_memory(conn, solid["code"], helpful=False)
    h.eq(feedback["unhelpful_count"], 1, "feedback actualiza unhelpful_count")


def _dedupe(h: Harness) -> None:
    conn = h.conn
    old = add_memory(
        conn,
        title="Comando de test",
        content="Ejecutar python3 tests/run.py.",
        key="test-command",
        source="user",
    )
    new = add_memory(
        conn,
        title="Comando de test",
        content="Ejecutar python3 tests/run.py --strict-budget.",
        key="test-command",
        source="user",
    )
    h.eq(
        get_memory(conn, old["code"])["status"],
        "superseded",
        "misma key activa se marca superseded",
    )
    h.eq(
        get_memory(conn, old["code"])["superseded_by"],
        new["code"],
        "la anterior enlaza a la sustituta",
    )
    h.eq(
        get_memory(conn, new["code"])["supersedes_id"],
        get_memory(conn, old["code"])["id"],
        "la nueva enlaza a la anterior",
    )
    h.raises(
        ValidationError,
        lambda: add_memory(
            conn,
            title="Comando de test",
            content="Ejecutar python3 tests/run.py --strict-budget.",
            key="otra-key",
            source="user",
        ),
        "el duplicado exacto se rechaza",
    )
    third = add_memory(
        conn,
        title="Otro comando",
        content="Ejecutar uvx ruff check .",
        key="ruff-command",
        source="user",
    )
    superseded = supersede_memory(conn, third["code"], new["code"])
    h.eq(superseded["status"], "superseded", "supersede_memory marca la anterior")
    h.raises(
        ValidationError,
        lambda: supersede_memory(conn, third["code"], new["code"]),
        "no se puede superseder dos veces",
    )


def _budget(h: Harness) -> None:
    conn = db.connect(h.tmp / "budget.db")
    try:
        config = {"project_budget_chars": 100}
        first = add_memory(
            conn,
            title="Entrada grande",
            content="A" * 80,
            key="grande",
            source="user",
            config=config,
        )
        h.eq(first["budget"]["used"], 80, "el presupuesto cuenta caracteres")
        try:
            add_memory(
                conn,
                title="Entrada extra",
                content="B" * 30,
                key="extra",
                source="user",
                config=config,
            )
        except BudgetError as exc:
            h.ok(exc.chars_to_free == 10, "el error calcula los caracteres a liberar")
            h.ok(bool(exc.current_entries), "el error lista las entradas actuales")
            h.ok(
                "Libera al menos 10" in str(exc),
                "el mensaje indica cuánto liberar",
            )
        else:
            raise AssertionError("el presupuesto debía bloquear la entrada extra")

        replacement = add_memory(
            conn,
            title="Entrada grande",
            content="C" * 90,
            key="grande",
            source="user",
            config=config,
        )
        h.eq(
            replacement["status"],
            "active",
            "un batch que libera y añade a la vez pasa",
        )
        h.eq(replacement["budget"]["used"], 90, "el presupuesto usa solo la vigente")
        h.eq(
            get_memory(conn, first["code"])["status"],
            "superseded",
            "la entrada liberada queda superseded",
        )
    finally:
        conn.close()


def _ledger(h: Harness) -> None:
    conn = h.conn
    memory = add_memory(
        conn,
        title="Estado original",
        content="Contenido original para el ledger.",
        key="ledger-memory",
        source="user",
    )
    patch_memory(conn, memory["code"], title="Estado cambiado", actor="tester")
    h.eq(get_memory(conn, memory["code"])["title"], "Estado cambiado", "el patch se aplica")
    row = conn.execute(
        "SELECT id FROM memory_ledger WHERE memory_id = ? AND action = 'update' "
        "ORDER BY id DESC LIMIT 1",
        (memory["id"],),
    ).fetchone()
    h.ok(row is not None, "el patch deja asiento en el ledger")
    result = memory_ledger.rollback(conn, int(row["id"]), actor="tester")
    h.ok(result["restored"], "el rollback restaura un estado anterior")
    h.eq(
        get_memory(conn, memory["code"])["title"],
        "Estado original",
        "el rollback recupera el título original",
    )
    h.ok(
        conn.execute(
            "SELECT 1 FROM memory_ledger WHERE id = ? AND action = 'rollback'",
            (result["ledger_id"],),
        ).fetchone()
        is not None,
        "el rollback registra un asiento nuevo",
    )

    created = add_memory(
        conn,
        title="Memoria efímera",
        content="Se creará y se deshará.",
        key="efimera",
        source="user",
    )
    create_row = conn.execute(
        "SELECT id FROM memory_ledger WHERE memory_id = ? AND action = 'create' "
        "ORDER BY id DESC LIMIT 1",
        (created["id"],),
    ).fetchone()
    h.ok(create_row is not None, "la creación deja asiento")
    undone = memory_ledger.rollback(conn, int(create_row["id"]))
    h.ok(not undone["restored"], "el rollback de una creación no restaura fila")
    h.raises(
        ValidationError,
        lambda: get_memory(conn, created["code"]),
        "el rollback de una creación elimina la memoria",
    )


def run(h: Harness) -> None:
    _migration(h)
    _crud(h)
    _policy(h)
    _dedupe(h)
    _budget(h)
    _ledger(h)
