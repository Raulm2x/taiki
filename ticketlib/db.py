"""Conexión, esquema y migraciones de SQLite."""

from __future__ import annotations

import os
import sqlite3
import tempfile
from contextlib import contextmanager
from pathlib import Path

from . import paths
from .domain import SCHEMA_VERSION, TicketError
from .util import AGENT_ALIASES, now

AGENTS_NORMALIZED_KEY = "agents_normalized"

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS tickets (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    code         TEXT NOT NULL UNIQUE,
    title        TEXT NOT NULL,
    body         TEXT NOT NULL DEFAULT '',
    kind         TEXT NOT NULL DEFAULT 'otro',
    status       TEXT NOT NULL DEFAULT 'nuevo',
    priority     TEXT NOT NULL DEFAULT 'media',
    project      TEXT,
    path         TEXT,
    created_by   TEXT NOT NULL DEFAULT 'agente',
    assigned_to  TEXT,
    resolution   TEXT,
    iterations   INTEGER NOT NULL DEFAULT 0,
    parent_id    INTEGER REFERENCES tickets(id),
    archived     INTEGER NOT NULL DEFAULT 0,
    owner_session TEXT,
    heartbeat_at  TEXT,
    lease_until   TEXT,
    paths         TEXT,
    created_at   TEXT NOT NULL,
    updated_at   TEXT NOT NULL,
    started_at   TEXT,
    completed_at TEXT,
    confirmed_at TEXT
);

CREATE INDEX IF NOT EXISTS idx_tickets_status    ON tickets(status);
CREATE INDEX IF NOT EXISTS idx_tickets_project   ON tickets(project);
CREATE INDEX IF NOT EXISTS idx_tickets_priority  ON tickets(priority);
CREATE INDEX IF NOT EXISTS idx_tickets_archived  ON tickets(archived);
CREATE INDEX IF NOT EXISTS idx_tickets_parent_id ON tickets(parent_id);
CREATE INDEX IF NOT EXISTS idx_tickets_updated   ON tickets(updated_at);

CREATE TABLE IF NOT EXISTS events (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    ticket_id   INTEGER NOT NULL REFERENCES tickets(id) ON DELETE CASCADE,
    ts          TEXT NOT NULL,
    actor       TEXT NOT NULL,
    actor_type  TEXT NOT NULL DEFAULT 'agente',
    session_id  TEXT,
    action      TEXT NOT NULL,
    from_status TEXT,
    to_status   TEXT,
    note        TEXT
);

CREATE INDEX IF NOT EXISTS idx_events_ticket ON events(ticket_id);
CREATE INDEX IF NOT EXISTS idx_events_ts     ON events(ts);
CREATE INDEX IF NOT EXISTS idx_events_action ON events(action);

CREATE TABLE IF NOT EXISTS comments (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    ticket_id INTEGER NOT NULL REFERENCES tickets(id) ON DELETE CASCADE,
    ts        TEXT NOT NULL,
    author    TEXT NOT NULL,
    body      TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_comments_ticket ON comments(ticket_id);

CREATE TABLE IF NOT EXISTS links (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    ticket_id  INTEGER NOT NULL REFERENCES tickets(id) ON DELETE CASCADE,
    kind       TEXT NOT NULL,
    ref        TEXT NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE(ticket_id, kind, ref)
);

CREATE INDEX IF NOT EXISTS idx_links_ticket ON links(ticket_id);

CREATE TABLE IF NOT EXISTS mcp_servers (
    pid          INTEGER PRIMARY KEY,
    ppid         INTEGER,
    name         TEXT NOT NULL DEFAULT 'tickets',
    fingerprint  TEXT,
    version      TEXT,
    started_at   TEXT NOT NULL,
    heartbeat_at TEXT NOT NULL,
    connection   TEXT,
    workspace    TEXT
);

CREATE TABLE IF NOT EXISTS tags (
    ticket_id INTEGER NOT NULL REFERENCES tickets(id) ON DELETE CASCADE,
    tag       TEXT NOT NULL,
    PRIMARY KEY (ticket_id, tag)
);

CREATE VIRTUAL TABLE IF NOT EXISTS tickets_fts USING fts5(
    title,
    body,
    code UNINDEXED,
    content='tickets',
    content_rowid='id',
    tokenize='unicode61 remove_diacritics 2'
);

CREATE TRIGGER IF NOT EXISTS tickets_fts_ai AFTER INSERT ON tickets BEGIN
    INSERT INTO tickets_fts(rowid, title, body, code)
    VALUES (new.id, new.title, new.body, new.code);
END;

CREATE TRIGGER IF NOT EXISTS tickets_fts_ad AFTER DELETE ON tickets BEGIN
    INSERT INTO tickets_fts(tickets_fts, rowid, title, body, code)
    VALUES ('delete', old.id, old.title, old.body, old.code);
END;

DROP TRIGGER IF EXISTS tickets_fts_au;
CREATE TRIGGER tickets_fts_au
AFTER UPDATE OF title, body, code ON tickets BEGIN
    INSERT INTO tickets_fts(tickets_fts, rowid, title, body, code)
    VALUES ('delete', old.id, old.title, old.body, old.code);
    INSERT INTO tickets_fts(rowid, title, body, code)
    VALUES (new.id, new.title, new.body, new.code);
END;
"""

# Esquema v5: memoria semántica. Se define como sentencias sueltas para que
# ``_migrate_v5`` pueda reaplicarlas de forma idempotente dentro de una
# transacción (``executescript`` haría un commit implícito).
MEMORY_DDL: tuple[str, ...] = (
    """
CREATE TABLE IF NOT EXISTS memories (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    code            TEXT NOT NULL UNIQUE,
    scope           TEXT NOT NULL DEFAULT 'project',
    kind            TEXT NOT NULL,
    "key"           TEXT NOT NULL,
    title           TEXT NOT NULL,
    description     TEXT NOT NULL DEFAULT '',
    content         TEXT NOT NULL,
    paths           TEXT,
    kinds           TEXT,
    ticket_kinds    TEXT,
    status          TEXT NOT NULL DEFAULT 'proposed',
    confidence      REAL NOT NULL DEFAULT 0.5,
    source          TEXT NOT NULL DEFAULT 'agent',
    actor           TEXT,
    session_id      TEXT,
    ticket_id       TEXT,
    supersedes_id   INTEGER REFERENCES memories(id),
    superseded_by   TEXT,
    pinned          INTEGER NOT NULL DEFAULT 0,
    locked          INTEGER NOT NULL DEFAULT 0,
    use_count       INTEGER NOT NULL DEFAULT 0,
    helpful_count   INTEGER NOT NULL DEFAULT 0,
    unhelpful_count INTEGER NOT NULL DEFAULT 0,
    last_used_at    TEXT,
    last_briefed_at TEXT,
    created_at      TEXT NOT NULL,
    updated_at      TEXT NOT NULL,
    archived_at     TEXT,
    fingerprint     TEXT NOT NULL
)
""",
    """
CREATE TABLE IF NOT EXISTS memory_evidence (
    memory_id  INTEGER NOT NULL REFERENCES memories(id) ON DELETE CASCADE,
    kind       TEXT NOT NULL,
    ref        TEXT NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE(memory_id, kind, ref)
)
""",
    """
CREATE TABLE IF NOT EXISTS memory_files (
    memory_id INTEGER NOT NULL REFERENCES memories(id) ON DELETE CASCADE,
    path      TEXT NOT NULL,
    content   TEXT NOT NULL,
    sha256    TEXT NOT NULL,
    PRIMARY KEY (memory_id, path)
)
""",
    """
CREATE TABLE IF NOT EXISTS memory_usage (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    ts         TEXT NOT NULL,
    memory_id  INTEGER NOT NULL REFERENCES memories(id) ON DELETE CASCADE,
    session_id TEXT,
    action     TEXT NOT NULL,
    helpful    INTEGER,
    note       TEXT
)
""",
    """
CREATE TABLE IF NOT EXISTS memory_ledger (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    ts          TEXT NOT NULL,
    actor       TEXT,
    actor_type  TEXT NOT NULL DEFAULT 'agente',
    session_id  TEXT,
    action      TEXT NOT NULL,
    memory_id   INTEGER,
    before_hash TEXT,
    after_hash  TEXT,
    reason      TEXT,
    meta        TEXT
)
""",
    """
CREATE TABLE IF NOT EXISTS memory_blobs (
    sha256     TEXT PRIMARY KEY,
    content    TEXT NOT NULL,
    created_at TEXT NOT NULL
)
""",
    "CREATE INDEX IF NOT EXISTS idx_memories_scope_status_kind "
    "ON memories(scope, status, kind)",
    'CREATE INDEX IF NOT EXISTS idx_memories_key ON memories("key")',
    "CREATE INDEX IF NOT EXISTS idx_memories_ticket ON memories(ticket_id)",
    "CREATE INDEX IF NOT EXISTS idx_memories_fingerprint "
    "ON memories(fingerprint)",
    "CREATE INDEX IF NOT EXISTS idx_memory_evidence_ref "
    "ON memory_evidence(ref)",
    "CREATE INDEX IF NOT EXISTS idx_memory_usage_memory "
    "ON memory_usage(memory_id, ts)",
    """
CREATE VIRTUAL TABLE IF NOT EXISTS memories_fts USING fts5(
    title,
    description,
    content,
    "key",
    code,
    content='memories',
    content_rowid='id',
    tokenize='unicode61 remove_diacritics 2'
)
""",
    """
CREATE TRIGGER IF NOT EXISTS memories_fts_ai AFTER INSERT ON memories BEGIN
    INSERT INTO memories_fts(rowid, title, description, content, "key", code)
    VALUES (new.id, new.title, new.description, new.content, new."key", new.code);
END
""",
    """
CREATE TRIGGER IF NOT EXISTS memories_fts_ad AFTER DELETE ON memories BEGIN
    INSERT INTO memories_fts(memories_fts, rowid, title, description, content, "key", code)
    VALUES ('delete', old.id, old.title, old.description, old.content, old."key", old.code);
END
""",
    """
CREATE TRIGGER IF NOT EXISTS memories_fts_au
AFTER UPDATE OF title, description, content, "key", code ON memories BEGIN
    INSERT INTO memories_fts(memories_fts, rowid, title, description, content, "key", code)
    VALUES ('delete', old.id, old.title, old.description, old.content, old."key", old.code);
    INSERT INTO memories_fts(rowid, title, description, content, "key", code)
    VALUES (new.id, new.title, new.description, new.content, new."key", new.code);
END
""",
)

MEMORY_SCHEMA_SQL = ";\n".join(sql.strip() for sql in MEMORY_DDL) + ";"
SCHEMA_SQL = SCHEMA_SQL + "\n" + MEMORY_SCHEMA_SQL


def db_path(start: str | Path | None = None) -> Path:
    """Prioridad: TAIKI_DB/TICKETS_DB > .taiki del proyecto > global."""
    return paths.resolve_db(start)


def connect(
    path: str | Path | None = None,
    start: str | Path | None = None,
) -> sqlite3.Connection:
    target = Path(path) if path is not None else db_path(start)
    memory = str(target) == ":memory:"

    if not memory:
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            if os.environ.get("TAIKI_TMP_DB", "").lower() not in (
                "1",
                "true",
                "yes",
            ):
                raise TicketError(
                    "No se puede crear el directorio de la base de datos "
                    f"{target.parent}: {exc}. Define TAIKI_DB a una ruta "
                    "escribible o TAIKI_TMP_DB=1 para usar una DB temporal."
                ) from exc
            fallback = Path(tempfile.gettempdir()) / "taiki" / "taiki.db"
            target = fallback
            target.parent.mkdir(parents=True, exist_ok=True)
        try:
            os.chmod(target.parent, 0o700)
        except OSError:
            pass

    conn = sqlite3.connect(
        ":memory:" if memory else str(target),
        timeout=10,
        isolation_level=None,
    )
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA busy_timeout=5000")
    migrate(conn)

    if not memory:
        try:
            os.chmod(target, 0o600)
        except OSError:
            pass
    return conn


FTS_UPDATE_TRIGGER_SQL = (
    "DROP TRIGGER IF EXISTS tickets_fts_au",
    """
CREATE TRIGGER tickets_fts_au
AFTER UPDATE OF title, body, code ON tickets BEGIN
    INSERT INTO tickets_fts(tickets_fts, rowid, title, body, code)
    VALUES ('delete', old.id, old.title, old.body, old.code);
    INSERT INTO tickets_fts(rowid, title, body, code)
    VALUES (new.id, new.title, new.body, new.code);
END
""",
)

V4_INDEXES = (
    "CREATE INDEX IF NOT EXISTS idx_events_ts ON events(ts)",
    "CREATE INDEX IF NOT EXISTS idx_events_action ON events(action)",
    "CREATE INDEX IF NOT EXISTS idx_tickets_updated ON tickets(updated_at)",
)


def schema_version(conn: sqlite3.Connection) -> int:
    """Versión real del esquema (fuente única)."""
    return int(conn.execute("PRAGMA user_version").fetchone()[0])


def _table_columns(conn: sqlite3.Connection, table: str) -> set[str]:
    return {row["name"] for row in conn.execute(f"PRAGMA table_info({table})")}


def _has_table(conn: sqlite3.Connection, table: str) -> bool:
    return bool(
        conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?",
            (table,),
        ).fetchone()
    )


def _migrate_v2(conn: sqlite3.Connection, force: bool = False) -> None:
    """Columnas de claim/rutas, índice y fixup de alias (una sola vez)."""
    columns = _table_columns(conn, "tickets")
    if "iterations" not in columns:
        conn.execute(
            "ALTER TABLE tickets ADD COLUMN iterations INTEGER NOT NULL DEFAULT 0"
        )
    for column in ("owner_session", "heartbeat_at", "lease_until", "paths"):
        if column not in columns:
            conn.execute(f"ALTER TABLE tickets ADD COLUMN {column} TEXT")
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_tickets_owner ON tickets(owner_session)"
    )
    if _has_table(conn, "mcp_servers") and "workspace" not in _table_columns(
        conn, "mcp_servers"
    ):
        conn.execute("ALTER TABLE mcp_servers ADD COLUMN workspace TEXT")
    if force or not meta_get(conn, AGENTS_NORMALIZED_KEY):
        _normalize_agent_aliases(conn)
        meta_set(conn, AGENTS_NORMALIZED_KEY, "1")


def _migrate_v3(conn: sqlite3.Connection, force: bool = False) -> None:
    """Trigger FTS limitado a title/body/code."""
    for sql in FTS_UPDATE_TRIGGER_SQL:
        conn.execute(sql)


def _migrate_v4(conn: sqlite3.Connection, force: bool = False) -> None:
    """Índices de events(ts/action) y tickets(updated_at)."""
    for sql in V4_INDEXES:
        conn.execute(sql)


def _migrate_v5(conn: sqlite3.Connection, force: bool = False) -> None:
    """Memoria semántica: tablas, índices, FTS5 y sus triggers."""
    for sql in MEMORY_DDL:
        conn.execute(sql)
    meta_set(
        conn,
        "next_memory_number",
        meta_get(conn, "next_memory_number", "1") or "1",
    )


def _migrate_v6(conn: sqlite3.Connection, force: bool = False) -> None:
    """Etiquetas de tipo de ticket en la memoria (aditiva, sin backfill)."""
    if "ticket_kinds" not in _table_columns(conn, "memories"):
        conn.execute("ALTER TABLE memories ADD COLUMN ticket_kinds TEXT")


MIGRATIONS = {
    2: _migrate_v2,
    3: _migrate_v3,
    4: _migrate_v4,
    5: _migrate_v5,
    6: _migrate_v6,
}


def migrate(conn: sqlite3.Connection, force: bool = False) -> None:
    """Aplica el esquema y las migraciones pendientes, paso a paso.

    ``PRAGMA user_version`` es la única fuente de versión. En régimen estable
    ``connect`` no ejecuta DDL ni el fixup de alias. ``force`` reaplica los
    pasos (útil en tests de migración).
    """
    current = schema_version(conn)
    if current > SCHEMA_VERSION:
        raise TicketError(
            f"La base de datos usa el esquema v{current}, más reciente que "
            f"v{SCHEMA_VERSION}. Actualiza taiki y vuelve a intentarlo."
        )
    if not force and current >= SCHEMA_VERSION:
        return

    # Base nueva: no hay tabla de tickets. El esquema actual ya incluye todo.
    if not _table_columns(conn, "tickets"):
        conn.executescript(SCHEMA_SQL)
        with tx(conn):
            meta_set(conn, "next_ticket_number", "1")
            meta_set(conn, "next_memory_number", "1")
            meta_set(conn, "created_at", now())
        conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
        return

    start = current
    if start == 0:
        # DB anterior a user_version: meta.schema_version solo sirve de pista.
        row = conn.execute(
            "SELECT value FROM meta WHERE key = 'schema_version'"
        ).fetchone()
        start = int(row["value"]) if row else 1
        if start > SCHEMA_VERSION:
            raise TicketError(
                f"La base de datos usa el esquema v{start}, más reciente que "
                f"v{SCHEMA_VERSION}. Actualiza taiki y vuelve a intentarlo."
            )
    if force:
        start = min(start, 1)
    for version in range(start + 1, SCHEMA_VERSION + 1):
        step = MIGRATIONS.get(version)
        if step is None:
            continue
        with tx(conn):
            step(conn, force=force)
        conn.execute(f"PRAGMA user_version = {version}")


def _normalize_agent_aliases(conn: sqlite3.Connection) -> None:
    for alias, canonical in AGENT_ALIASES.items():
        for table, column in (
            ("tickets", "created_by"),
            ("tickets", "assigned_to"),
            ("events", "actor"),
            ("comments", "author"),
        ):
            conn.execute(
                f"UPDATE {table} SET {column} = ? WHERE lower({column}) = ?",
                (canonical, alias),
            )


@contextmanager
def tx(conn: sqlite3.Connection):
    """Transacción de escritura con bloqueo inmediato, reentrante."""
    if conn.in_transaction:
        yield
        return
    conn.execute("BEGIN IMMEDIATE")
    try:
        yield
    except BaseException:
        conn.execute("ROLLBACK")
        raise
    else:
        conn.execute("COMMIT")


def meta_get(
    conn: sqlite3.Connection, key: str, default: str | None = None
) -> str | None:
    row = conn.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
    return row["value"] if row else default


def meta_set(conn: sqlite3.Connection, key: str, value: str) -> None:
    conn.execute(
        "INSERT INTO meta(key, value) VALUES (?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (key, value),
    )
