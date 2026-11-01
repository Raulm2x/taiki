"""Claims por sesión, leases y un ticket activo por sesión."""

from __future__ import annotations

import argparse
import contextlib
import io
import threading
import time
from datetime import datetime, timedelta
from pathlib import Path

from ticketlib import db, domain, repo
from ticketlib.guard.context import find_active
from ticketlib.util import lease_expired
from tests.support import Harness


def _past(minutes: int = 5) -> str:
    moment = datetime.now().astimezone() - timedelta(minutes=minutes)
    return moment.isoformat(timespec="seconds")


def run(h: Harness) -> None:
    conn = h.conn

    # ── Dos sesiones del mismo agente no comparten ticket ───────────────
    shared = repo.create_ticket(conn, title="Trabajo compartido")
    repo.claim_ticket(
        conn, shared["code"], agent="opencode", session_id="ses_a"
    )
    h.raises(
        domain.TransitionError,
        lambda: repo.claim_ticket(
            conn, shared["code"], agent="opencode", session_id="ses_b"
        ),
        "otra sesión del mismo agente no puede reclamar el ticket",
    )
    again = repo.claim_ticket(
        conn, shared["code"], agent="opencode", session_id="ses_a"
    )
    h.ok(
        again["status"] == "en_progreso",
        "la sesión dueña puede refrescar su claim",
    )
    h.ok(
        find_active(conn, [], "opencode") is None,
        "sin sesión no se adopta un ticket reclamado por una sesión real",
    )

    # ── Claim anónimo: dueño estable por agente ─────────────────────────
    anon = repo.create_ticket(conn, title="Claim anónimo")
    first_anon = repo.claim_ticket(
        conn,
        anon["code"],
        agent="opencode",
        session_id=None,
        anonymous_owner=True,
    )
    h.eq(
        first_anon["owner_session"],
        "agent:opencode",
        "un claim sin sesión usa un dueño estable agent:<nombre>",
    )
    h.raises(
        domain.TransitionError,
        lambda: repo.claim_ticket(
            conn,
            anon["code"],
            agent="claude",
            session_id=None,
            anonymous_owner=True,
        ),
        "otro agente no puede quedarse un claim anónimo ajeno",
    )
    h.eq(
        repo.claim_ticket(
            conn,
            anon["code"],
            agent="opencode",
            session_id=None,
            anonymous_owner=True,
        )["owner_session"],
        "agent:opencode",
        "el mismo agente refresca su claim anónimo",
    )
    h.eq(
        find_active(conn, [], "opencode")["code"],
        anon["code"],
        "find_active recupera el claim anónimo del propio agente",
    )
    h.ok(
        find_active(conn, [], "claude") is None,
        "find_active no adopta el claim de otro agente",
    )
    repo.complete_ticket(
        conn,
        anon["code"],
        resolution="anónimo cerrado",
        actor="opencode",
        session_id=None,
    )
    h.ok(
        lease_expired("2020-01-01T00:00:00"),
        "lease_expired admite fechas naive (compatibilidad)",
    )
    h.ok(not lease_expired(None), "sin lease no se considera vencido")

    # ── assigned_to informativo: otro agente reclama si no hay dueño ────
    handed = repo.create_ticket(
        conn, title="Asignada al conductor", assigned_to="opencode"
    )
    taken = repo.claim_ticket(
        conn, handed["code"], agent="worker", session_id="ses_w"
    )
    try:
        repo.claim_ticket(
            conn, handed["code"], agent="claude", session_id="ses_x"
        )
        rejected = False
    except domain.TransitionError:
        rejected = True
    h.ok(
        taken["assigned_to"] == "worker"
        and taken["owner_session"] == "ses_w"
        and rejected,
        "otro agente reclama sin owner aunque assigned_to sea ajeno; "
        "con owner vigente se rechaza",
    )

    # ── Un solo ticket activo por sesión ────────────────────────────────
    second = repo.create_ticket(conn, title="Segundo trabajo")
    h.raises(
        domain.TransitionError,
        lambda: repo.claim_ticket(
            conn, second["code"], agent="opencode", session_id="ses_a"
        ),
        "una sesión no puede tener dos tickets en progreso",
    )
    repo.complete_ticket(
        conn, shared["code"], resolution="listo", session_id="ses_a"
    )
    claimed = repo.claim_ticket(
        conn, second["code"], agent="opencode", session_id="ses_a"
    )
    h.eq(
        claimed["owner_session"],
        "ses_a",
        "al cerrar el anterior la sesión puede reclamar otro",
    )
    h.ok(
        claimed["lease_until"] is not None,
        "el claim guarda un lease",
    )

    # ── Claim caducado y recuperación ───────────────────────────────────
    conn.execute(
        "UPDATE tickets SET heartbeat_at = ?, lease_until = ? WHERE code = ?",
        (_past(), _past(), second["code"]),
    )
    conn.commit()
    stale = repo.stale_claims(conn)
    h.ok(
        any(item["code"] == second["code"] for item in stale),
        "stale_claims detecta el lease vencido",
    )
    h.raises(
        domain.TransitionError,
        lambda: repo.claim_ticket(
            conn, second["code"], agent="opencode", session_id="ses_b"
        ),
        "un claim caducado no se toma sin --steal",
    )
    stolen = repo.claim_ticket(
        conn,
        second["code"],
        agent="opencode",
        session_id="ses_b",
        steal=True,
    )
    h.eq(
        stolen["owner_session"],
        "ses_b",
        "--steal recupera el claim caducado",
    )
    h.raises(
        domain.TransitionError,
        lambda: repo.claim_ticket(
            conn,
            second["code"],
            agent="opencode",
            session_id="ses_otra",
            steal=True,
        ),
        "--steal rechaza un lease todavía vigente",
    )
    forced = repo.claim_ticket(
        conn,
        second["code"],
        agent="opencode",
        session_id="ses_otra",
        force=True,
    )
    h.eq(
        forced["owner_session"],
        "ses_otra",
        "--force sí puede forzar un lease vigente",
    )
    conn.execute(
        "UPDATE tickets SET heartbeat_at = ?, lease_until = ? WHERE code = ?",
        (_past(), _past(), second["code"]),
    )
    conn.commit()
    recovered = repo.claim_ticket(
        conn,
        second["code"],
        agent="usuario",
        session_id="ses_humano",
        steal=True,
    )
    h.ok(
        recovered["assigned_to"] == "usuario"
        and recovered["owner_session"] == "ses_humano",
        "--steal también recupera un ticket asignado a otro agente",
    )
    repo.claim_ticket(
        conn,
        second["code"],
        agent="opencode",
        session_id="ses_b",
        force=True,
    )

    # ── Heartbeat renueva el lease ──────────────────────────────────────
    conn.execute(
        "UPDATE tickets SET heartbeat_at = ?, lease_until = ? WHERE code = ?",
        (_past(10), _past(10), second["code"]),
    )
    conn.commit()
    renewed = repo.heartbeat_ticket(
        conn, second["code"], "ses_b", min_interval=0
    )
    token = repo.get_ticket(conn, second["code"])
    h.ok(
        renewed and not repo.stale_claims(conn),
        "el heartbeat renueva el lease",
    )
    h.ok(
        not repo.heartbeat_ticket(conn, second["code"], "ses_c", min_interval=0),
        "otra sesión no puede renovar el lease",
    )
    h.eq(
        repo.get_ticket(conn, second["code"])["owner_session"],
        "ses_b",
        "el heartbeat no cambia el dueño",
    )
    conn.execute(
        "UPDATE tickets SET heartbeat_at = ?, lease_until = ? WHERE code = ?",
        (_past(5), _past(5), second["code"]),
    )
    conn.commit()
    from commands.heartbeat import cmd_heartbeat

    with contextlib.redirect_stdout(io.StringIO()):
        code = cmd_heartbeat(
            argparse.Namespace(code=second["code"], minutes=10)
        )
    h.eq(code, 0, "taiki heartbeat renueva el lease por CLI")
    h.ok(
        not repo.stale_claims(conn),
        "el lease renovado por CLI deja de estar caducado",
    )

    # ── find_active prioriza al dueño ───────────────────────────────────
    repo.comment_ticket(conn, second["code"], "nota", author="opencode")
    h.ok(
        find_active(conn, ["ses_b"], "opencode")["code"] == second["code"],
        "find_active encuentra el ticket por owner_session",
    )
    h.ok(
        find_active(conn, ["ses_x"], "opencode") is None,
        "las sesiones ajenas no heredan el ticket por eventos",
    )

    # ── Cerrar exige la sesión dueña (o force) ──────────────────────────
    h.raises(
        domain.PermissionDenied,
        lambda: repo.complete_ticket(
            conn,
            second["code"],
            resolution="cierre ajeno",
            session_id="ses_c",
        ),
        "otra sesión no puede completar el ticket",
    )
    h.raises(
        domain.PermissionDenied,
        lambda: repo.complete_ticket(
            conn, second["code"], resolution="cierre sin sesión"
        ),
        "una sesión sin identificar no completa un ticket reclamado",
    )

    # ── Cerrar libera el claim ──────────────────────────────────────────
    repo.complete_ticket(
        conn, second["code"], resolution="terminado", session_id="ses_b"
    )
    closed = repo.get_ticket(conn, second["code"])
    h.ok(
        closed["owner_session"] is None and closed["lease_until"] is None,
        "completar libera owner_session y lease",
    )

    # ── Parser de claim con --steal ─────────────────────────────────────
    parsed = __import__("parser").build_parser().parse_args(
        ["claim", "T-0001", "--steal"]
    )
    h.ok(parsed.steal, "el CLI acepta claim --steal")

    # ── Migración de una DB anterior sin columnas de claim ──────────────
    legacy = Path(h.tmp) / "legacy.db"
    older = db.connect(legacy)
    older.execute("DROP INDEX IF EXISTS idx_tickets_owner")
    for column in ("owner_session", "heartbeat_at", "lease_until", "paths"):
        older.execute(f"ALTER TABLE tickets DROP COLUMN {column}")
    # Simula una DB v1 real: user_version nunca se fijó.
    older.execute("PRAGMA user_version = 0")
    older.close()
    upgraded = db.connect(legacy)
    columns = {row["name"] for row in upgraded.execute("PRAGMA table_info(tickets)")}
    h.ok(
        {"owner_session", "heartbeat_at", "lease_until", "paths"} <= columns,
        "migrate añade las columnas de claim y rutas a una DB existente",
    )
    indexes = {row["name"] for row in upgraded.execute("PRAGMA index_list(tickets)")}
    h.ok(
        "idx_tickets_owner" in indexes,
        "migrate crea el índice de owner_session",
    )
    h.eq(
        upgraded.execute("PRAGMA user_version").fetchone()[0],
        domain.SCHEMA_VERSION,
        "migrate sella la versión del esquema en user_version",
    )
    upgraded.close()

    # ── Matriz de migraciones por pasos: v1/v2/v3 → v4 ─────────────────
    for start in (1, 2, 3):
        path = Path(h.tmp) / f"migrate-v{start}.db"
        seed = db.connect(path)
        seed.execute("DROP INDEX IF EXISTS idx_events_ts")
        seed.execute("DROP INDEX IF EXISTS idx_events_action")
        seed.execute("DROP INDEX IF EXISTS idx_tickets_updated")
        if start < 3:
            seed.execute("DROP TRIGGER IF EXISTS tickets_fts_au")
        seed.execute(f"PRAGMA user_version = {start}")
        seed.close()
        stepped = db.connect(path)
        version = stepped.execute("PRAGMA user_version").fetchone()[0]
        names = {
            row["name"]
            for row in stepped.execute(
                "SELECT name FROM sqlite_master WHERE type = 'index'"
            )
        }
        trigger = stepped.execute(
            "SELECT sql FROM sqlite_master WHERE name = 'tickets_fts_au'"
        ).fetchone()
        h.eq(
            version,
            domain.SCHEMA_VERSION,
            f"v{start} migra hasta v{domain.SCHEMA_VERSION}",
        )
        h.ok(
            {"idx_events_ts", "idx_events_action", "idx_tickets_updated"}
            <= names,
            f"v{start} recupera los índices de v4",
        )
        h.ok(
            trigger is not None
            and "UPDATE OF title, body, code" in trigger["sql"],
            f"v{start} recupera el trigger FTS de v3",
        )
        stepped.close()

    # ── connect no espera detrás de otro escritor en régimen estable ────
    writer = db.connect(legacy)
    writer.execute("BEGIN IMMEDIATE")
    elapsed: dict[str, float] = {}

    def opener() -> None:
        started = time.perf_counter()
        opened = db.connect(legacy)
        elapsed["seconds"] = time.perf_counter() - started
        opened.close()

    thread = threading.Thread(target=opener)
    thread.start()
    thread.join(timeout=5)
    writer.execute("ROLLBACK")
    writer.close()
    h.ok(
        not thread.is_alive() and elapsed.get("seconds", 99.0) < 1.0,
        "db.connect no espera al lock de otro escritor",
    )
