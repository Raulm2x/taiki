"""Curado determinista: caducidad, archivo, dedupe, presupuesto y ledger."""

from __future__ import annotations

from datetime import datetime, timedelta

from ticketlib import db
from ticketlib.memory import add_memory, curate
from ticketlib.memory.models import BUDGET_STATUSES
from tests.support import Harness

_BASE = {
    "candidate_similarity": 0.9,
    "stale_after_days": 10,
    "archive_after_days": 20,
}


def _ago(days: int) -> str:
    return (
        datetime.now().astimezone() - timedelta(days=days)
    ).isoformat(timespec="seconds")


def _set_age(conn, code: str, days: int) -> None:
    stamp = _ago(days)
    conn.execute(
        "UPDATE memories SET created_at = ?, updated_at = ?, last_used_at = ? "
        "WHERE code = ?",
        (stamp, stamp, stamp, code),
    )


def _aging(h: Harness) -> None:
    conn = db.connect(h.tmp / "curate-aging.db")
    try:
        recent = add_memory(
            conn,
            title="Uso reciente",
            content="Se usó esta semana.",
            key="reciente",
            source="user",
        )
        old = add_memory(
            conn,
            title="Uso antiguo",
            content="Hace mucho que no se usa.",
            key="antigua",
            source="user",
        )
        pinned = add_memory(
            conn,
            title="Fijada antigua",
            content="Pin aunque no se use.",
            key="fijada",
            source="user",
            pinned=True,
        )
        locked = add_memory(
            conn,
            title="Bloqueada antigua",
            content="Lock aunque no se use.",
            key="bloqueada",
            source="user",
            locked=True,
        )
        stale_old = add_memory(
            conn,
            title="Stale antigua",
            content="Ya estaba caducada.",
            key="stale-antigua",
            source="user",
        )
        stale_recent = add_memory(
            conn,
            title="Stale reciente",
            content="Caducó hace poco.",
            key="stale-reciente",
            source="user",
        )
        for code in (old["code"], pinned["code"], locked["code"]):
            _set_age(conn, code, 30)
        for code in (stale_old["code"], stale_recent["code"]):
            conn.execute(
                "UPDATE memories SET status = 'stale', updated_at = ? WHERE code = ?",
                (_ago(30 if code == stale_old["code"] else 1), code),
            )
        conn.commit()

        result = curate(conn, _BASE)
        status = {
            row["code"]: row["status"]
            for row in conn.execute(
                "SELECT code, status FROM memories ORDER BY code"
            )
        }
        h.ok(
            result["stale"] == [old["code"]]
            and result["archived"] == [stale_old["code"]],
            "active→stale por inactividad y stale→archived por antigüedad",
        )
        h.ok(
            status[recent["code"]] == "active"
            and status[old["code"]] == "stale"
            and status[stale_old["code"]] == "archived"
            and status[stale_recent["code"]] == "stale",
            "la memoria reciente sigue activa y la stale reciente se conserva",
        )
        h.ok(
            status[pinned["code"]] == "active"
            and status[locked["code"]] == "active",
            "pin y lock quedan exentos de stale",
        )
        actions = {
            row["action"]
            for row in conn.execute("SELECT action FROM memory_ledger")
        }
        h.ok(
            {"stale", "archive"} <= actions,
            "cada transición deja asiento en el ledger",
        )
    finally:
        conn.close()


def _dedupe(h: Harness) -> None:
    conn = db.connect(h.tmp / "curate-dedupe.db")
    try:
        exact_a = add_memory(
            conn, title="Duplicado exacto", content="Contenido uno.", key="dup-a",
            source="user",
        )
        exact_b = add_memory(
            conn, title="Otro título", content="Otro contenido.", key="dup-b",
            source="user",
        )
        conn.execute(
            "UPDATE memories SET fingerprint = ? WHERE code = ?",
            (exact_a["fingerprint"], exact_b["code"]),
        )

        key_a = add_memory(
            conn, title="Clave repetida A", content="Contenido A.", key="clave-a",
            source="user",
        )
        key_b = add_memory(
            conn, title="Clave repetida B", content="Contenido B.", key="clave-b",
            source="user",
        )
        conn.execute(
            'UPDATE memories SET "key" = ? WHERE code = ?',
            (key_a["key"], key_b["code"]),
        )

        near_a = add_memory(
            conn,
            title="Rama de integración",
            content="develop integra con ff-only y rebase antes de mergear.",
            key="near-a",
            source="user",
        )
        near_b = add_memory(
            conn,
            title="Rama de integración",
            content="develop integra con ff-only y rebase siempre antes de mergear.",
            key="near-b",
            source="user",
        )

        pin_a = add_memory(
            conn, title="Duplicado pin", content="Contenido pin.", key="pin-a",
            source="user",
        )
        pin_b = add_memory(
            conn, title="Duplicado pin dos", content="Otro pin.", key="pin-b",
            source="user",
        )
        conn.execute(
            "UPDATE memories SET fingerprint = ? WHERE code = ?",
            (pin_a["fingerprint"], pin_b["code"]),
        )
        conn.execute("UPDATE memories SET pinned = 1 WHERE code = ?", (pin_b["code"],))
        conn.commit()

        before = conn.execute("SELECT COUNT(*) FROM memories").fetchone()[0]
        result = curate(conn, _BASE)
        after = conn.execute("SELECT COUNT(*) FROM memories").fetchone()[0]
        status = {
            row["code"]: row["status"]
            for row in conn.execute(
                "SELECT code, status FROM memories ORDER BY code"
            )
        }
        merged = {item["code"]: item["into"] for item in result["merged"]}
        h.ok(
            merged.get(exact_b["code"]) == exact_a["code"]
            and merged.get(key_b["code"]) == key_a["code"],
            "dedupe exacto y por clave archivan el perdedor",
        )
        h.ok(
            near_b["code"] in merged or near_a["code"] in merged,
            "near-duplicate por similitud se consolida",
        )
        h.ok(
            status[pin_b["code"]] == "active"
            and status[pin_a["code"]] == "archived",
            "el duplicado pinned gana y el no protegido se archiva",
        )
        h.ok(
            before == after
            and conn.execute(
                "SELECT 1 FROM memory_ledger WHERE action = 'merge' LIMIT 1"
            ).fetchone()
            is not None,
            "el curado nunca borra filas y el merge deja asiento",
        )
    finally:
        conn.close()


def _dry_run(h: Harness) -> None:
    conn = db.connect(h.tmp / "curate-dry.db")
    try:
        old = add_memory(
            conn, title="Antigua dry", content="No debe cambiar.", key="dry",
            source="user",
        )
        _set_age(conn, old["code"], 30)
        conn.commit()
        ledger_before = conn.execute("SELECT COUNT(*) FROM memory_ledger").fetchone()[0]

        result = curate(conn, _BASE, dry_run=True)
        status = conn.execute(
            "SELECT status FROM memories WHERE code = ?", (old["code"],)
        ).fetchone()[0]
        ledger_after = conn.execute("SELECT COUNT(*) FROM memory_ledger").fetchone()[0]
        h.ok(
            result["stale"] == [old["code"]]
            and status == "active"
            and ledger_after == ledger_before,
            "dry_run informa del cambio previsto sin escribir estado ni ledger",
        )

        real = curate(conn, _BASE)
        h.eq(real["stale"], [old["code"]], "el curado real aplica el plan")
    finally:
        conn.close()


def _budget(h: Harness) -> None:
    conn = db.connect(h.tmp / "curate-budget.db")
    try:
        pinned = add_memory(
            conn,
            title="Pin grande",
            content="P" * 30,
            key="presupuesto-pin",
            source="user",
            pinned=True,
            config={"project_budget_chars": 1000},
        )
        low = add_memory(
            conn,
            title="Baja prioridad",
            content="A" * 20,
            key="presupuesto-baja",
            source="user",
            confidence=0.1,
            config={"project_budget_chars": 1000},
        )
        high = add_memory(
            conn,
            title="Alta prioridad",
            content="B" * 20,
            key="presupuesto-alta",
            source="user",
            confidence=0.9,
            config={"project_budget_chars": 1000},
        )
        cfg = {
            "project_budget_chars": 40,
            "stale_after_days": 1000,
            "archive_after_days": 1000,
            "candidate_similarity": 0.99,
        }
        result = curate(conn, cfg)
        budget = result["budget"]["project"]
        status = {
            row["code"]: row["status"]
            for row in conn.execute(
                "SELECT code, status FROM memories ORDER BY code"
            )
        }
        h.ok(
            budget["used_before"] == 70
            and budget["used"] <= budget["limit"]
            and budget["over"] == 0,
            "el presupuesto se reduce hasta el límite y queda dentro",
        )
        h.ok(
            budget["retired"] == [low["code"], high["code"]]
            and low["code"] in result["archived"]
            and high["code"] in result["archived"],
            "se archiva primero la menor prioridad y se reporta",
        )
        live = conn.execute(
            "SELECT COUNT(*) FROM memories WHERE status IN (?, ?, ?)",
            BUDGET_STATUSES,
        ).fetchone()[0]
        h.ok(
            status[pinned["code"]] == "active"
            and status[low["code"]] == "archived"
            and status[high["code"]] == "archived"
            and live == 1,
            "el recorte nunca borra ni toca el pin y solo sobrevive el protegido",
        )
    finally:
        conn.close()


def run(h: Harness) -> None:
    _aging(h)
    _dedupe(h)
    _dry_run(h)
    _budget(h)
