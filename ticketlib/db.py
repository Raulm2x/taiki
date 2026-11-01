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
        except OSError:
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


def migrate(conn: sqlite3.Connection, force: bool = False) -> None:
    """Aplica el esquema y las migraciones pendientes.

    La versión vive en ``PRAGMA user_version``; en régimen estable ``connect``
    no ejecuta DDL ni el fixup de alias, así que no toma el lock de escritura.
    ``force`` reaplica todo (útil en tests de migración).
    """
    current = int(conn.execute("PRAGMA user_version").fetchone()[0])
    if current > SCHEMA_VERSION:
        raise TicketError(
            f"La base de datos usa el esquema v{current}, más reciente que "
            f"v{SCHEMA_VERSION}. Actualiza taiki y vuelve a intentarlo."
        )
    if not force and current >= SCHEMA_VERSION:
        return

    conn.executescript(SCHEMA_SQL)
    # Columnas aditivas: se añaden antes de crear índices sobre ellas.
    columns = {col["name"] for col in conn.execute("PRAGMA table_info(tickets)")}
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

    row = conn.execute("SELECT value FROM meta WHERE key = 'schema_version'").fetchone()
    version = int(row["value"]) if row else 0
    if version > SCHEMA_VERSION:
        raise TicketError(
            f"La base de datos usa el esquema v{version}, más reciente que "
            f"v{SCHEMA_VERSION}. Actualiza taiki y vuelve a intentarlo."
        )
    server_columns = {
        col["name"] for col in conn.execute("PRAGMA table_info(mcp_servers)")
    }
    if "workspace" not in server_columns:
        conn.execute("ALTER TABLE mcp_servers ADD COLUMN workspace TEXT")

    with tx(conn):
        if row is None:
            meta_set(conn, "schema_version", str(SCHEMA_VERSION))
            meta_set(conn, "next_ticket_number", "1")
            meta_set(conn, "created_at", now())
        else:
            meta_set(conn, "schema_version", str(SCHEMA_VERSION))
        # Los agentes internos de OpenCode (build, plan…) no son dueños
        # distintos. El fixup recorre cuatro columnas: se hace una sola vez.
        if row is not None and (
            force or not meta_get(conn, AGENTS_NORMALIZED_KEY)
        ):
            _normalize_agent_aliases(conn)
            meta_set(conn, AGENTS_NORMALIZED_KEY, "1")
    conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")


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
