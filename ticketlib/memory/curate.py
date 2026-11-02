"""Curado determinista de la memoria: caducidad, archivo, dedupe y presupuesto.

No usa LLM, no borra filas y no aprueba propuestas. Cada cambio de estado deja
un asiento en el ledger con el snapshot anterior y posterior, de modo que sea
recuperable. ``dry_run`` ejecuta el mismo plan dentro de un savepoint y lo
revierte al terminar: el informe es real, la base no cambia.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta
from difflib import SequenceMatcher
from typing import Any

from .. import db
from ..util import now, parse_datetime
from . import core
from . import ledger as ledger_lib
from .budget import priority_key
from .models import BUDGET_STATUSES, normalize_fingerprint

ACTOR = "curado-determinista"
_LIVE_STATUSES = BUDGET_STATUSES


def _datetime(value: Any) -> datetime | None:
    return parse_datetime(value)


def _activity(memory: dict[str, Any]) -> datetime | None:
    """Última señal de uso o verificación de una memoria."""
    for name in ("last_used_at", "updated_at", "created_at"):
        parsed = _datetime(memory.get(name))
        if parsed is not None:
            return parsed
    return None


def _live_rows(conn: sqlite3.Connection, scope: str | None = None) -> list[dict[str, Any]]:
    """Memorias que aún participan en el curado, ordenadas por código."""
    placeholders = ", ".join("?" for _ in _LIVE_STATUSES)
    sql = (
        "SELECT * FROM memories "
        f"WHERE status IN ({placeholders})"
    )
    params: list[Any] = list(_LIVE_STATUSES)
    if scope is not None:
        sql += " AND scope = ?"
        params.append(scope)
    sql += " ORDER BY code"
    return [dict(row) for row in conn.execute(sql, params).fetchall()]


def _protected(memory: dict[str, Any]) -> bool:
    """Pin y lock son decisiones humanas: el curado no las toca."""
    return bool(memory.get("pinned") or memory.get("locked"))


def _set_status(
    conn: sqlite3.Connection,
    memory: dict[str, Any],
    status: str,
    *,
    action: str,
    reason: str,
    meta: dict[str, Any] | None = None,
) -> None:
    """Cambia el estado y registra la transición en el ledger."""
    memory_id = int(memory["id"])
    before = ledger_lib.snapshot(conn, memory_id)
    timestamp = now()
    if status == "archived":
        conn.execute(
            "UPDATE memories SET status = ?, archived_at = ?, updated_at = ? "
            "WHERE id = ?",
            (status, timestamp, timestamp, memory_id),
        )
    else:
        conn.execute(
            "UPDATE memories SET status = ?, archived_at = NULL, updated_at = ? "
            "WHERE id = ?",
            (status, timestamp, memory_id),
        )
    after = ledger_lib.snapshot(conn, memory_id)
    ledger_lib.record(
        conn,
        action=action,
        memory_id=memory_id,
        before=before,
        after=after,
        actor=ACTOR,
        reason=reason,
        meta=meta,
    )


def _curation_key(memory: dict[str, Any]) -> tuple[Any, ...]:
    """Ganador de un duplicado: pin/lock, estado vigente y prioridad."""
    rank = {"active": 0, "stale": 1, "proposed": 2}
    return (
        0 if _protected(memory) else 1,
        rank.get(str(memory.get("status")), 3),
        priority_key(memory),
    )


def _similar(left: dict[str, Any], right: dict[str, Any], threshold: float) -> bool:
    """Near-duplicate por título+contenido normalizados."""
    if str(left.get("fingerprint") or "") == str(right.get("fingerprint") or ""):
        return True
    if (
        left.get("key")
        and right.get("key")
        and str(left["key"]) == str(right["key"])
    ):
        return True
    if str(left.get("kind") or "") != str(right.get("kind") or ""):
        return False
    left_text = normalize_fingerprint(left.get("title"), left.get("content"))
    right_text = normalize_fingerprint(right.get("title"), right.get("content"))
    if not left_text or not right_text:
        return False
    return SequenceMatcher(None, left_text, right_text).ratio() >= threshold


def _merge(
    conn: sqlite3.Connection,
    loser: dict[str, Any],
    winner: dict[str, Any],
    *,
    reason: str,
) -> None:
    """Absorbe evidencia y ficheros del perdedor y lo archiva."""
    winner_id = int(winner["id"])
    loser_id = int(loser["id"])
    before_winner = ledger_lib.snapshot(conn, winner_id)
    conn.execute(
        "INSERT OR IGNORE INTO memory_evidence(memory_id, kind, ref, created_at) "
        "SELECT ?, kind, ref, created_at FROM memory_evidence WHERE memory_id = ?",
        (winner_id, loser_id),
    )
    conn.execute(
        "INSERT OR IGNORE INTO memory_files(memory_id, path, content, sha256) "
        "SELECT ?, path, content, sha256 FROM memory_files WHERE memory_id = ?",
        (winner_id, loser_id),
    )
    conn.execute(
        "UPDATE memories SET updated_at = ? WHERE id = ?",
        (now(), winner_id),
    )
    after_winner = ledger_lib.snapshot(conn, winner_id)
    ledger_lib.record(
        conn,
        action="update",
        memory_id=winner_id,
        before=before_winner,
        after=after_winner,
        actor=ACTOR,
        reason=f"absorbidas evidencias de {loser['code']}",
        meta={"counterpart_id": loser_id},
    )
    _set_status(
        conn,
        loser,
        "archived",
        action="merge",
        reason=reason,
        meta={"into": str(winner["code"]), "counterpart_id": winner_id},
    )


def _dedupe(
    conn: sqlite3.Connection,
    cfg: dict[str, Any],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Consolida duplicados por scope y archiva el perdedor."""
    threshold = float(cfg["candidate_similarity"])
    merged: list[dict[str, Any]] = []
    report: list[dict[str, Any]] = []
    rows = _live_rows(conn)
    by_scope: dict[str, list[dict[str, Any]]] = {}
    for memory in rows:
        by_scope.setdefault(str(memory.get("scope") or "project"), []).append(memory)

    for scope in sorted(by_scope):
        kept: list[dict[str, Any]] = []
        for memory in sorted(by_scope[scope], key=_curation_key):
            duplicate = next(
                (
                    candidate
                    for candidate in kept
                    if _similar(candidate, memory, threshold)
                ),
                None,
            )
            if duplicate is None:
                kept.append(memory)
                continue
            if _protected(memory):
                # Aunque coincida, pin/lock quedan exentos de dedupe.
                kept.append(memory)
                continue
            reason = (
                f"duplicado de {duplicate['code']} "
                f"(scope {scope}, fingerprint/clave/similitud)"
            )
            _merge(conn, memory, duplicate, reason=reason)
            merged.append({"code": str(memory["code"]), "into": str(duplicate["code"])})
            report.append(
                {
                    "code": str(memory["code"]),
                    "scope": scope,
                    "action": "merge",
                    "from_status": str(memory.get("status")),
                    "to_status": "archived",
                    "into": str(duplicate["code"]),
                    "reason": reason,
                }
            )
    return merged, report


def _age(
    conn: sqlite3.Connection,
    cfg: dict[str, Any],
    at: datetime,
) -> tuple[list[str], list[str], list[dict[str, Any]]]:
    """active→stale por inactividad y stale→archived por antigüedad."""
    stale_days = int(cfg["stale_after_days"])
    archive_days = int(cfg["archive_after_days"])
    stale: list[str] = []
    archived: list[str] = []
    report: list[dict[str, Any]] = []
    for memory in _live_rows(conn):
        if _protected(memory):
            continue
        status = str(memory.get("status"))
        if status == "active":
            reference = _activity(memory)
            if reference is None or reference > at - timedelta(days=stale_days):
                continue
            reason = (
                f"sin uso ni actualización desde "
                f"{reference.isoformat(timespec='seconds')} "
                f"(> {stale_days} días)"
            )
            _set_status(conn, memory, "stale", action="stale", reason=reason)
            stale.append(str(memory["code"]))
            report.append(
                {
                    "code": str(memory["code"]),
                    "scope": str(memory.get("scope") or "project"),
                    "action": "stale",
                    "from_status": "active",
                    "to_status": "stale",
                    "reason": reason,
                }
            )
        elif status == "stale":
            reference = _datetime(memory.get("updated_at"))
            if reference is None or reference > at - timedelta(days=archive_days):
                continue
            reason = (
                f"stale desde {reference.isoformat(timespec='seconds')} "
                f"(> {archive_days} días)"
            )
            _set_status(conn, memory, "archived", action="archive", reason=reason)
            archived.append(str(memory["code"]))
            report.append(
                {
                    "code": str(memory["code"]),
                    "scope": str(memory.get("scope") or "project"),
                    "action": "archive",
                    "from_status": "stale",
                    "to_status": "archived",
                    "reason": reason,
                }
            )
    return stale, archived, report


def _budget(
    conn: sqlite3.Connection,
    cfg: dict[str, Any],
) -> tuple[dict[str, Any], list[str], list[dict[str, Any]]]:
    """Archiva la menor prioridad hasta respetar el presupuesto de cada scope."""
    data: dict[str, Any] = {}
    retired: list[str] = []
    report: list[dict[str, Any]] = []
    for scope in ("project", "user"):
        limit = int(
            cfg["user_budget_chars"]
            if scope == "user"
            else cfg["project_budget_chars"]
        )
        rows = _live_rows(conn, scope)
        before = sum(len(str(memory.get("content") or "")) for memory in rows)
        used = before
        retired_scope: list[str] = []
        if used > limit:
            for memory in sorted(rows, key=priority_key, reverse=True):
                if used <= limit:
                    break
                if _protected(memory):
                    continue
                reason = (
                    f"presupuesto de {scope} excedido: "
                    f"{before} > {limit} caracteres"
                )
                _set_status(
                    conn, memory, "archived", action="archive", reason=reason
                )
                used -= len(str(memory.get("content") or ""))
                retired_scope.append(str(memory["code"]))
                retired.append(str(memory["code"]))
                report.append(
                    {
                        "code": str(memory["code"]),
                        "scope": scope,
                        "action": "archive",
                        "from_status": str(memory.get("status")),
                        "to_status": "archived",
                        "reason": reason,
                    }
                )
        data[scope] = {
            "limit": limit,
            "used_before": before,
            "used": used,
            "over": max(0, used - limit),
            "retired": retired_scope,
        }
    return data, retired, report


def curate(
    conn: sqlite3.Connection,
    config: dict[str, Any] | None = None,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Ejecuta el curado determinista y devuelve el informe de cambios.

    ``config`` acepta el bloque ``memory``, el config completo o ``None`` para
    cargar los defaults del workspace. ``dry_run`` no escribe nada.
    """
    cfg = core.config(config)
    at = _datetime(now())
    if at is None:  # pragma: no cover - now() siempre es ISO válido
        raise RuntimeError("No se pudo resolver la hora actual del curado")
    if dry_run:
        conn.execute("SAVEPOINT curate_dry_run")
    try:
        with db.tx(conn):
            merged, merge_report = _dedupe(conn, cfg)
            stale, archived_by_age, age_report = _age(conn, cfg, at)
            budget, retired, budget_report = _budget(conn, cfg)
            archived = archived_by_age + retired
            report = merge_report + age_report + budget_report
            return {
                "stale": stale,
                "archived": archived,
                "merged": merged,
                "budget": budget,
                "report": report,
            }
    finally:
        if dry_run:
            conn.execute("ROLLBACK TO curate_dry_run")
            conn.execute("RELEASE curate_dry_run")


__all__ = ["curate"]
