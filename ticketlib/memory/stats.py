"""Estadísticas e impacto de la memoria: agregados deterministas y estables.

``stats`` describe el estado actual (estado/scope/kind, presupuesto,
propuestas, uso y routing). ``impact`` resume tendencias de tickets cerrados
por semanas. Ninguno escribe memoria ni afirma causalidad: son datos para
decidir, no una explicación del resultado.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta
from typing import Any

from ..util import now, parse_datetime
from . import core
from . import budget as budget_lib
from .models import (
    MEMORY_KINDS,
    MEMORY_SCOPES,
    MEMORY_STATUSES,
    USAGE_ACTIONS,
)

CLOSED_STATUSES = ("completado", "verificado", "confirmado", "cancelado")
GENERIC_PROFILES = ("", "agente", "usuario", "conductor")


def _counts(conn: sqlite3.Connection) -> tuple[int, dict[str, int], dict[str, int], dict[str, int]]:
    by_status = {status: 0 for status in MEMORY_STATUSES}
    by_scope = {scope: 0 for scope in MEMORY_SCOPES}
    by_kind = {kind: 0 for kind in MEMORY_KINDS}
    total = 0
    for row in conn.execute(
        "SELECT status, scope, kind, COUNT(*) AS total FROM memories "
        "GROUP BY status, scope, kind"
    ):
        count = int(row["total"])
        total += count
        by_status[str(row["status"])] = by_status.get(str(row["status"]), 0) + count
        by_scope[str(row["scope"])] = by_scope.get(str(row["scope"]), 0) + count
        by_kind[str(row["kind"])] = by_kind.get(str(row["kind"]), 0) + count
    return total, by_status, by_scope, by_kind


def _budget(conn: sqlite3.Connection, cfg: dict[str, Any]) -> dict[str, Any]:
    return {
        scope: budget_lib.summary(
            core.scope_entries(conn, scope),
            core.limit_for(cfg, scope),
            scope,
        )
        for scope in ("project", "user")
    }


def _proposals(conn: sqlite3.Connection) -> dict[str, Any]:
    rows = conn.execute(
        "SELECT code, created_at FROM memories WHERE status = 'proposed' "
        "ORDER BY code"
    ).fetchall()
    codes = [str(row["code"]) for row in rows]
    oldest_days: int | None = None
    if rows:
        base = parse_datetime(now())
        oldest = min(
            (
                parsed
                for parsed in (
                    parse_datetime(row["created_at"]) for row in rows
                )
                if parsed is not None
            ),
            default=None,
        )
        if base is not None and oldest is not None:
            oldest_days = max(0, (base - oldest).days)
    return {"count": len(codes), "codes": codes, "oldest_days": oldest_days}


def _usage(conn: sqlite3.Connection) -> dict[str, Any]:
    by_action = {action: 0 for action in USAGE_ACTIONS}
    for row in conn.execute(
        "SELECT action, COUNT(*) AS total FROM memory_usage GROUP BY action"
    ):
        by_action[str(row["action"])] = int(row["total"])
    totals = conn.execute(
        "SELECT COALESCE(SUM(use_count), 0) AS use_count, "
        "COALESCE(SUM(helpful_count), 0) AS helpful, "
        "COALESCE(SUM(unhelpful_count), 0) AS unhelpful, "
        "MAX(last_used_at) AS last_used_at FROM memories"
    ).fetchone()
    return {
        "by_action": by_action,
        "events": sum(by_action.values()),
        "use_count": int(totals["use_count"]),
        "helpful": int(totals["helpful"]),
        "unhelpful": int(totals["unhelpful"]),
        "last_used_at": totals["last_used_at"],
    }


def _routing(conn: sqlite3.Connection) -> dict[str, Any]:
    placeholders = ", ".join("?" for _ in CLOSED_STATUSES)
    rows = conn.execute(
        "SELECT kind, assigned_to, COUNT(*) AS samples, "
        "SUM(CASE WHEN status != 'cancelado' AND iterations <= 1 THEN 1 ELSE 0 END) "
        "AS first_pass, AVG(iterations) AS avg_attempts FROM tickets "
        f"WHERE archived = 0 AND status IN ({placeholders}) "
        "AND lower(trim(COALESCE(assigned_to, ''))) NOT IN (?, ?, ?, ?) "
        "GROUP BY kind, assigned_to ORDER BY samples DESC, kind, assigned_to",
        (*CLOSED_STATUSES, *GENERIC_PROFILES),
    ).fetchall()
    groups: list[dict[str, Any]] = []
    for row in rows:
        samples = int(row["samples"])
        first_pass = int(row["first_pass"] or 0)
        groups.append(
            {
                "kind": str(row["kind"] or "otro"),
                "profile": str(row["assigned_to"]),
                "samples": samples,
                "first_pass": first_pass,
                "first_pass_rate": round(first_pass / samples, 3) if samples else 0.0,
                "avg_attempts": (
                    round(float(row["avg_attempts"]), 2)
                    if row["avg_attempts"] is not None
                    else None
                ),
            }
        )
    return {
        "samples": sum(item["samples"] for item in groups),
        "groups": groups,
    }


def stats(
    conn: sqlite3.Connection,
    config: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Foto estable de la memoria: conteos, presupuesto, uso y routing."""
    cfg = core.config(config)
    total, by_status, by_scope, by_kind = _counts(conn)
    return {
        "generated_at": now(),
        "total": total,
        "by_status": by_status,
        "by_scope": by_scope,
        "by_kind": by_kind,
        "budget": _budget(conn, cfg),
        "proposals": _proposals(conn),
        "usage": _usage(conn),
        "routing": _routing(conn),
    }


def _hours(start: Any, end: Any) -> float | None:
    left = parse_datetime(start)
    right = parse_datetime(end)
    if left is None or right is None:
        return None
    seconds = (right - left).total_seconds()
    if seconds < 0:
        return None
    return round(seconds / 3600.0, 2)


def _period(value: Any) -> str | None:
    parsed = parse_datetime(value)
    if parsed is None:
        return None
    iso = parsed.isocalendar()
    return f"{iso.year}-W{iso.week:02d}"


def impact(
    conn: sqlite3.Connection,
    config: dict[str, Any] | None = None,
    days: int | None = None,
) -> dict[str, Any]:
    """Tendencias semanales de cierre, intentos, lead time y verificación."""
    cfg = core.config(config)
    window = int(days if days is not None else cfg["candidate_lookback_days"])
    if isinstance(window, bool) or window <= 0:
        from ..domain import ValidationError

        raise ValidationError("Los días de impacto deben ser mayores que 0")
    base = parse_datetime(now())
    if base is None:  # pragma: no cover - now() siempre es ISO válido
        raise RuntimeError("No se pudo resolver la hora actual")
    cutoff = (base - timedelta(days=window)).isoformat(timespec="seconds")

    rows = conn.execute(
        "SELECT status, iterations, started_at, completed_at FROM tickets "
        "WHERE archived = 0 AND completed_at IS NOT NULL AND completed_at >= ? "
        "ORDER BY completed_at",
        (cutoff,),
    ).fetchall()
    buckets: dict[str, dict[str, Any]] = {}
    attempts_total = 0
    lead_total = 0.0
    lead_samples = 0
    for row in rows:
        attempts = int(row["iterations"] or 0)
        attempts_total += attempts
        lead = _hours(row["started_at"], row["completed_at"])
        if lead is not None:
            lead_total += lead
            lead_samples += 1
        period = _period(row["completed_at"])
        if period is None:
            continue
        bucket = buckets.setdefault(
            period,
            {
                "period": period,
                "closed": 0,
                "attempts_total": 0,
                "lead_total": 0.0,
                "lead_samples": 0,
                "verified": 0,
                "verification_failures": 0,
            },
        )
        bucket["closed"] += 1
        bucket["attempts_total"] += attempts
        if lead is not None:
            bucket["lead_total"] += lead
            bucket["lead_samples"] += 1

    verified = 0
    failures = 0
    for row in conn.execute(
        "SELECT ts, from_status, to_status FROM events WHERE ts >= ? "
        "AND (to_status = 'verificado' OR "
        "(from_status = 'verificado' AND to_status = 'en_progreso'))",
        (cutoff,),
    ):
        period = _period(row["ts"])
        is_failure = (
            row["from_status"] == "verificado" and row["to_status"] == "en_progreso"
        )
        if is_failure:
            failures += 1
        else:
            verified += 1
        if period is None:
            continue
        bucket = buckets.setdefault(
            period,
            {
                "period": period,
                "closed": 0,
                "attempts_total": 0,
                "lead_total": 0.0,
                "lead_samples": 0,
                "verified": 0,
                "verification_failures": 0,
            },
        )
        bucket["verified"] += 0 if is_failure else 1
        bucket["verification_failures"] += 1 if is_failure else 0

    trend = []
    for period in sorted(buckets):
        bucket = buckets[period]
        closed = int(bucket.pop("closed"))
        attempts_sum = int(bucket.pop("attempts_total"))
        lead_sum = float(bucket.pop("lead_total"))
        lead_count = int(bucket.pop("lead_samples"))
        bucket.update(
            {
                "closed": closed,
                "avg_attempts": round(attempts_sum / closed, 2) if closed else None,
                "avg_lead_time_hours": (
                    round(lead_sum / lead_count, 2) if lead_count else None
                ),
            }
        )
        trend.append(bucket)

    closed = len(rows)
    return {
        "generated_at": now(),
        "days": window,
        "cutoff": cutoff,
        "closed": closed,
        "attempts": {
            "total": attempts_total,
            "avg": round(attempts_total / closed, 2) if closed else None,
        },
        "lead_time_hours": {
            "samples": lead_samples,
            "avg": round(lead_total / lead_samples, 2) if lead_samples else None,
        },
        "verification": {"verified": verified, "failures": failures},
        "trend": trend,
        "notes": ["Datos descriptivos; no implican causalidad."],
    }


__all__ = ["impact", "stats"]
