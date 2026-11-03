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
from ticketlib.memory.budget import select_for_budget
from tests.support import Harness


def _rejects(h: Harness, cases: list, message: str) -> None:
    """Agrupa casos de rechazo en una sola comprobación de presupuesto."""
    rejected = 0
    for case in cases:
        try:
            case()
        except ValidationError:
            rejected += 1
    h.eq(rejected, len(cases), message)


def _migration(h: Harness) -> None:
    """Una DB v4 conserva tickets y comentarios al migrar a v7."""
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
        assert db.schema_version(old) == 4
        db.migrate(old)
        h.ok(
            db.schema_version(old) == 7
            and repo.get_ticket(old, ticket["code"])["title"]
            == "Sobrevive a la migración"
            and old.execute(
                "SELECT 1 FROM comments WHERE ticket_id = ?", (ticket["id"],)
            ).fetchone()
            is not None,
            "v4→v7 migra memorias y conserva tickets y comentarios",
        )
        migrated = add_memory(
            old,
            title="Memoria migrada",
            content="El trigger FTS funciona después de migrar.",
            key="migrada",
            source="user",
        )
        h.ok(
            migrated["code"] == "M-0001"
            and old.execute(
                "SELECT 1 FROM memories_fts WHERE memories_fts MATCH 'trigger'"
            ).fetchone()
            is not None,
            "la DB migrada numera memorias y el FTS funciona",
        )
    finally:
        old.close()


def _ticket_kinds(h: Harness) -> None:
    """v5→v6, etiquetas de tipo de ticket, compatibilidad y filtros."""
    path = h.tmp / "v5.db"
    old = db.connect(path)
    try:
        seeded = add_memory(
            old,
            title="Memoria v5",
            content="Se creó antes de la columna ticket_kinds.",
            key="memoria-v5",
            source="user",
        )
        old.execute("ALTER TABLE memories DROP COLUMN ticket_kinds")
        old.execute("PRAGMA user_version = 5")
        db.migrate(old)
        h.ok(
            db.schema_version(old) == 7
            and "ticket_kinds"
            in {row["name"] for row in old.execute("PRAGMA table_info(memories)")}
            and get_memory(old, seeded["code"])["ticket_kinds"] == [],
            "v5→v6 añade ticket_kinds y las memorias antiguas quedan generales",
        )
    finally:
        old.close()

    conn = db.connect(h.tmp / "ticket-kinds.db")
    bug = add_memory(
        conn,
        title="Riesgo zeta en pagos",
        content="El riesgo zeta aparece al cobrar.",
        kind="pitfall",
        ticket_kinds=["bug", "funcionalidad", "bug"],
        source="user",
    )
    docs = add_memory(
        conn,
        title="Riesgo zeta en docs",
        content="El riesgo zeta aparece al documentar.",
        kind="pitfall",
        ticket_kinds=["documentacion"],
        source="user",
    )
    general = add_memory(
        conn,
        title="Riesgo zeta general",
        content="El riesgo zeta aplica a cualquier ticket.",
        kind="pitfall",
        source="user",
    )
    h.ok(
        bug["ticket_kinds"] == ["bug", "funcionalidad"]
        and docs["ticket_kinds"] == ["documentacion"]
        and general["ticket_kinds"] == []
        and {item["code"] for item in list_memories(conn, ticket_kinds=["bug"])}
        == {bug["code"], general["code"]}
        and {
            item["code"]
            for item in list_memories(conn, ticket_kinds=["bug", "documentacion"])
        }
        == {bug["code"], docs["code"], general["code"]},
        "add_memory persiste ticket_kinds sin duplicados y list los combina",
    )
    h.eq(
        {
            item["code"]
            for item in search_memories(
                conn, "zeta", ticket_kinds=["bug"], record_usage=False
            )
        },
        {bug["code"], general["code"]},
        "search_memories filtra por ticket_kinds con la misma compatibilidad",
    )
    patched = patch_memory(conn, bug["code"], ticket_kinds=["seguridad"])
    cleared = patch_memory(conn, bug["code"], ticket_kinds=[])
    h.ok(
        patched["ticket_kinds"] == ["seguridad"]
        and cleared["ticket_kinds"] == []
        and bug["code"]
        in {item["code"] for item in list_memories(conn, ticket_kinds=["bug"])},
        "patch_memory reemplaza las etiquetas y una lista vacía vuelve a general",
    )
    _rejects(
        h,
        [
            lambda: add_memory(
                conn,
                title="Tipo inválido",
                content="No debe guardarse.",
                ticket_kinds=["inventado"],
                source="user",
            ),
            lambda: list_memories(conn, ticket_kinds=["inventado"]),
        ],
        "un tipo de ticket inválido se rechaza al crear y al filtrar",
    )
    conn.close()


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
    h.ok(
        first["code"] == "M-0001"
        and first["status"] == "active"
        and first["source"] == "user"
        and first["budget"]["limit"] == 3000,
        "la primera memoria es M-0001 activa de usuario con presupuesto 3000",
    )

    user_memory = add_memory(
        conn,
        title="Preferencia de commits",
        content="Prefiero commits pequeños y con código en el asunto.",
        kind="preference",
        scope="user",
        source="user",
    )
    h.ok(
        user_memory["scope"] == "user"
        and len(list_memories(conn, scope="user")) == 1
        and list_memories(conn, scope="project")[0]["code"] == first["code"],
        "list_memories filtra por scope user y project",
    )

    h.eq(
        get_memory(conn, "M-1")["code"],
        first["code"],
        "get_memory normaliza M-1 a M-0001",
    )
    h.raises(
        ValidationError,
        lambda: get_memory(conn, "M-9999"),
        "get_memory falla con una memoria inexistente",
    )

    found = search_memories(conn, "develop", record_usage=False)
    accented = add_memory(
        conn,
        title="Configuración de ramas",
        content="Las ramas agent/* nacen de develop.",
        kind="convention",
        key="configuracion-ramas",
        source="user",
    )
    h.ok(
        any(item["code"] == first["code"] for item in found)
        and any(
            item["code"] == accented["code"]
            for item in search_memories(conn, "configuracion", record_usage=False)
        ),
        "la búsqueda FTS encuentra por contenido e ignora acentos",
    )
    h.ok(
        bool(search_memories(conn, "ramas", session_id="ses_test"))
        and conn.execute(
            "SELECT 1 FROM memory_usage WHERE action = 'search' LIMIT 1"
        ).fetchone()
        is not None,
        "search_memories devuelve resultados y registra uso",
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
    h.ok(
        solid["status"] == "active" and solid["ticket_id"] == "T-0045",
        "agent con 2 evidencias activa y rellena ticket_id",
    )

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
    negative_user = add_memory(
        conn,
        title="Preferencia negativa",
        content="Nunca uses force push en esta rama.",
        kind="preference",
        key="force-push-usuario",
        source="user",
    )
    h.ok(
        negative["status"] == "proposed"
        and negative_user["status"] == "proposed",
        "una regla negativa de agente o de usuario siempre queda propuesta",
    )

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
    h.ok(
        rejected["status"] == "archived"
        and list_memories(conn, status="archived")[0]["code"] == negative["code"],
        "reject_memory archiva y las archivadas se listan explícitamente",
    )

    pinned = pin_memory(conn, solid["code"])
    archived = archive_memory(conn, solid["code"])
    restored = restore_memory(conn, solid["code"])
    h.ok(
        pinned["pinned"]
        and archived["status"] == "archived"
        and restored["status"] == "active",
        "pin, archive y restore cambian el estado esperado",
    )
    unpinned = pin_memory(conn, solid["code"], pinned=False)
    h.ok(not unpinned["pinned"], "pin_memory puede desfijar")

    helpful = feedback_memory(conn, solid["code"], helpful=True, note="útil")
    unhelpful = feedback_memory(conn, solid["code"], helpful=False)
    h.ok(
        helpful["helpful_count"] == 1 and unhelpful["unhelpful_count"] == 1,
        "feedback actualiza helpful_count y unhelpful_count",
    )


def _status_explicit(h: Harness) -> None:
    conn = h.conn
    _rejects(
        h,
        [
            lambda: add_memory(
                conn,
                title="Estado forzado",
                content="No debería saltarse la política.",
                source="agent",
                status="active",
                evidence=[{"kind": "ticket", "ref": "T-0045"}],
            ),
            lambda: add_memory(
                conn,
                title="Superseded directa",
                content="No se crea una superseded a mano.",
                source="import",
                status="superseded",
            ),
        ],
        "no se puede forzar active ni crear superseded directamente",
    )
    archived = add_memory(
        conn,
        title="Importada archivada",
        content="Memoria antigua importada.",
        source="import",
        status="archived",
    )
    h.ok(
        archived["status"] == "archived"
        and bool(archived["archived_at"])
        and not any(
            item["code"] == archived["code"]
            for item in list_memories(conn, include_inactive=False)
        ),
        "import puede crear una archivada con archived_at fuera del listado",
    )


def _fingerprint(h: Harness) -> None:
    conn = h.conn
    cjk_one = add_memory(
        conn,
        title="记忆",
        content="第一段内容",
        key="cjk-uno",
        source="user",
    )
    cjk_two = add_memory(
        conn,
        title="记忆",
        content="第二段内容",
        key="cjk-dos",
        source="user",
    )
    h.ok(
        cjk_one["fingerprint"] != cjk_two["fingerprint"]
        and cjk_one["code"] != cjk_two["code"],
        "dos textos CJK distintos no colapsan en huella ni código",
    )
    emoji_one = add_memory(
        conn,
        title="🚀",
        content="🚀",
        key="emoji-uno",
        source="user",
    )
    emoji_two = add_memory(
        conn,
        title="🔥",
        content="🔥",
        key="emoji-dos",
        source="user",
    )
    h.ok(
        emoji_one["fingerprint"].startswith("sha256:")
        and emoji_one["fingerprint"] != emoji_two["fingerprint"],
        "un texto de símbolos usa hash estable y no colisiona",
    )


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
    h.ok(
        get_memory(conn, old["code"])["status"] == "superseded"
        and get_memory(conn, old["code"])["superseded_by"] == new["code"],
        "misma key activa se marca superseded y enlaza a la sustituta",
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
    h.ok(superseded["status"] == "superseded", "supersede_memory marca la anterior")
    h.raises(
        ValidationError,
        lambda: supersede_memory(conn, third["code"], new["code"]),
        "no se puede superseder dos veces",
    )


def _budget(h: Harness) -> None:
    conn = db.connect(h.tmp / "budget.db")
    try:
        config = {"project_budget_chars": 100, "enforce_budget": True}
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
            h.ok(
                exc.chars_to_free == 10
                and bool(exc.current_entries)
                and "Libera al menos 10" in str(exc),
                "el error calcula caracteres, lista entradas y explica el recorte",
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
        h.ok(
            replacement["status"] == "active"
            and replacement["budget"]["used"] == 90
            and get_memory(conn, first["code"])["status"] == "superseded",
            "un batch que libera y añade a la vez pasa y usa solo la vigente",
        )

        memories = [
            {
                "code": "M-0001",
                "scope": "project",
                "status": "active",
                "content": "x" * 10,
                "confidence": 0.9,
                "updated_at": "2026-01-01T00:00:00-05:00",
                "use_count": 9,
                "pinned": False,
            },
            {
                "code": "M-0002",
                "scope": "user",
                "status": "active",
                "content": "x" * 10,
                "confidence": 0.1,
                "updated_at": "2026-01-01T00:00:00-05:00",
                "use_count": 0,
                "pinned": False,
            },
            {
                "code": "M-0003",
                "scope": "project",
                "status": "active",
                "content": "x" * 10,
                "confidence": 0.1,
                "updated_at": "2025-01-01T00:00:00-05:00",
                "use_count": 0,
                "pinned": True,
            },
            {
                "code": "M-0004",
                "scope": "project",
                "status": "active",
                "content": "x" * 10,
                "confidence": 0.2,
                "updated_at": "2025-01-01T00:00:00-05:00",
                "use_count": 0,
                "pinned": False,
            },
        ]
        selected, dropped, used = select_for_budget(memories, 25)
        h.ok(
            [item["code"] for item in selected] == ["M-0003", "M-0002"]
            and used == 20
            and [item["code"] for item in dropped] == ["M-0001", "M-0004"],
            "la prioridad es pinned > user > confianza y respeta el presupuesto",
        )

        relaxed = {"project_budget_chars": 10, "enforce_budget": False}
        receipt = add_memory(
            conn,
            title="Exceso permitido",
            content="D" * 5,
            key="exceso",
            source="user",
            config=relaxed,
        )
        try:
            add_memory(
                conn,
                title="Exceso estricto",
                content="E" * 5,
                key="exceso-estricto",
                source="user",
                config={"project_budget_chars": 10, "enforce_budget": True},
            )
        except BudgetError:
            strict_raises = True
        else:
            strict_raises = False
        h.ok(
            receipt["budget"]["used"] > receipt["budget"]["limit"]
            and bool(receipt["budget_warning"])
            and "el brief selecciona por prioridad" in str(receipt["budget_warning"])
            and strict_raises,
            "enforce_budget=false deja el exceso como aviso; true lanza BudgetError",
        )
    finally:
        conn.close()


def _rollback_budget(h: Harness) -> None:
    conn = db.connect(h.tmp / "rollback-budget.db")
    try:
        cfg = {"project_budget_chars": 100, "enforce_budget": True}
        first = add_memory(
            conn,
            title="A",
            content="A" * 20,
            key="rb-a",
            source="user",
            config=cfg,
        )
        add_memory(
            conn,
            title="B",
            content="B" * 60,
            key="rb-b",
            source="user",
            config=cfg,
        )
        patch_memory(
            conn,
            first["code"],
            content="A" * 10,
            config=cfg,
        )
        add_memory(
            conn,
            title="C",
            content="C" * 30,
            key="rb-c",
            source="user",
            config=cfg,
        )
        row = conn.execute(
            "SELECT id FROM memory_ledger WHERE memory_id = ? AND action = 'update' "
            "ORDER BY id DESC LIMIT 1",
            (first["id"],),
        ).fetchone()
        h.ok(row is not None, "el patch deja asiento para el rollback con presupuesto")
        h.raises(
            BudgetError,
            lambda: memory_ledger.rollback(conn, int(row["id"]), config=cfg),
            "el rollback que excede el presupuesto se rechaza",
        )
        h.ok(
            get_memory(conn, first["code"])["content"] == "A" * 10
            and conn.execute("SELECT COUNT(*) FROM memories").fetchone()[0] == 3,
            "el rollback rechazado no cambia ni duplica memorias",
        )
    finally:
        conn.close()


def _supersede_rollback(h: Harness) -> None:
    conn = h.conn
    old = add_memory(
        conn,
        title="Memoria antigua",
        content="Contenido antiguo que será sustituido.",
        key="sr-old",
        source="user",
    )
    new = add_memory(
        conn,
        title="Memoria nueva",
        content="Contenido nuevo que sustituye.",
        key="sr-new",
        source="user",
    )
    supersede_memory(conn, old["code"], new["code"])
    h.eq(
        get_memory(conn, new["code"])["supersedes_id"],
        old["id"],
        "supersede enlaza la nueva con la antigua",
    )
    row = conn.execute(
        "SELECT id FROM memory_ledger WHERE memory_id = ? AND action = 'supersede' "
        "ORDER BY id DESC LIMIT 1",
        (old["id"],),
    ).fetchone()
    h.ok(row is not None, "supersede deja asiento de la memoria antigua")
    result = memory_ledger.rollback(conn, int(row["id"]))
    h.ok(
        result["restored"]
        and get_memory(conn, old["code"])["status"] == "active"
        and not get_memory(conn, old["code"])["superseded_by"]
        and not get_memory(conn, new["code"])["supersedes_id"],
        "el rollback de supersede restaura y limpia ambos enlaces",
    )


def _ledger(h: Harness) -> None:
    conn = h.conn
    memory = add_memory(
        conn,
        title="Estado original",
        content="Contenido original para el ledger.",
        key="ledger-memory",
        source="user",
    )
    patch_memory(
        conn,
        memory["code"],
        title="Estado cambiado",
        content="Contenido cambiado para el ledger.",
        actor="tester",
    )
    h.ok(
        get_memory(conn, memory["code"])["title"] == "Estado cambiado"
        and any(
            item["code"] == memory["code"]
            for item in search_memories(conn, "cambiado", record_usage=False)
        )
        and not any(
            item["code"] == memory["code"]
            for item in search_memories(conn, "original", record_usage=False)
        ),
        "el patch se aplica y reindexa el FTS con el título nuevo",
    )
    row = conn.execute(
        "SELECT id FROM memory_ledger WHERE memory_id = ? AND action = 'update' "
        "ORDER BY id DESC LIMIT 1",
        (memory["id"],),
    ).fetchone()
    h.ok(row is not None, "el patch deja asiento en el ledger")
    result = memory_ledger.rollback(conn, int(row["id"]), actor="tester")
    h.ok(
        result["restored"]
        and get_memory(conn, memory["code"])["title"] == "Estado original",
        "el rollback restaura el título original",
    )
    h.ok(
        any(
            item["code"] == memory["code"]
            for item in search_memories(conn, "original", record_usage=False)
        )
        and not any(
            item["code"] == memory["code"]
            for item in search_memories(conn, "cambiado", record_usage=False)
        ),
        "el rollback reindexa el título restaurado y quita el revertido",
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
    h.ok(
        create_row is not None
        and any(
            item["code"] == created["code"]
            for item in search_memories(conn, "deshara", record_usage=False)
        ),
        "la creación deja asiento y aparece en FTS antes del rollback",
    )
    undone = memory_ledger.rollback(conn, int(create_row["id"]))
    h.ok(not undone["restored"], "el rollback de una creación no restaura fila")
    h.raises(
        ValidationError,
        lambda: get_memory(conn, created["code"]),
        "el rollback de una creación elimina la memoria",
    )
    h.ok(
        not any(
            item["code"] == created["code"]
            for item in search_memories(conn, "deshara", record_usage=False)
        ),
        "el rollback de una creación limpia el FTS",
    )


def run(h: Harness) -> None:
    _migration(h)
    _ticket_kinds(h)
    _crud(h)
    _policy(h)
    _status_explicit(h)
    _fingerprint(h)
    _dedupe(h)
    _budget(h)
    _rollback_budget(h)
    _supersede_rollback(h)
    _ledger(h)
