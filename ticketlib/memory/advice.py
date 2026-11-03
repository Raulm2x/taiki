"""Routing observado y checklist del reviewer a partir de la memoria y el histórico.

Este módulo es la única fuente de las recomendaciones de routing y del checklist:
el MCP y cualquier otra capa solo lo invocan. Nunca cambia la configuración ni
lanza agentes: devuelve una sugerencia con su muestra y confianza para que el
conductor decida.
"""

from __future__ import annotations

import sqlite3
from typing import Any, Iterable

from .. import agents
from ..domain import KINDS, ValidationError
from ..util import check_enum
from . import core
from .candidates import wilson_lower
from .store import get_memory, list_memories

CLOSED_STATUSES = ("completado", "verificado", "confirmado", "cancelado")
CHECKLIST_KINDS = ("pitfall", "convention")
CHECKLIST_LIMIT = 5
GENERIC_PROFILES = {"", "agente", "usuario", "conductor"}


def _paths_overlap(left: str, right: str) -> bool:
    a = str(left or "").strip().strip("*").rstrip("/")
    b = str(right or "").strip().strip("*").rstrip("/")
    if not a or not b:
        return False
    return a == b or a.startswith(b + "/") or b.startswith(a + "/")


def _touches(ticket_paths: Iterable[object], wanted: Iterable[object]) -> bool:
    targets = [str(item) for item in wanted if str(item).strip()]
    if not targets:
        return True
    return any(
        _paths_overlap(str(path), target)
        for path in ticket_paths
        for target in targets
    )


def _first_pass(ticket: dict[str, Any]) -> bool:
    return (
        str(ticket.get("status")) != "cancelado"
        and int(ticket.get("iterations") or 0) <= 1
    )


def _plan_for(
    kind: str,
    profile: str,
    config: dict[str, Any] | None,
) -> tuple[Any, Any]:
    """Variante y steps del perfil observado, con fallback al routing actual."""
    table = agents.registry(config)
    row = table.get(profile)
    if row is not None:
        route = agents.route(kind, row)
        return route["variant"], row.get("steps", route["steps"])
    plan = agents.assign(kind, config)
    return plan["variant"], plan["steps"]


def routing_advice(
    conn: sqlite3.Connection,
    kind: str | None,
    paths: Iterable[object] | None = None,
    config: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    """Mejor perfil observado para ``(kind, paths)`` con muestra suficiente.

    Solo considera tickets cerrados con perfil asignado. Con ``paths`` usa los
    tickets que tocan esas rutas; si no hay muestra suficiente devuelve
    ``None`` en vez de extrapolar de rutas ajenas. No modifica la configuración.
    """
    if not kind:
        return None
    check_enum(kind, KINDS, "tipo")
    cfg = core.config(config)
    if not cfg["enabled"]:
        return None
    minimum = int(cfg["candidate_routing_min_samples"])
    wanted = [str(item) for item in paths or [] if str(item).strip()]
    placeholders = ", ".join("?" for _ in CLOSED_STATUSES)
    rows = conn.execute(
        f"SELECT * FROM tickets WHERE archived = 0 AND status IN ({placeholders})",
        CLOSED_STATUSES,
    ).fetchall()
    tickets = [
        memory
        for memory in (core.row_to_memory(row) for row in rows)
        if str(memory.get("kind")) == kind
        and str(memory.get("assigned_to") or "").strip() not in GENERIC_PROFILES
    ]
    if wanted:
        tickets = [
            ticket for ticket in tickets if _touches(ticket.get("paths") or [], wanted)
        ]
    groups: dict[str, list[dict[str, Any]]] = {}
    for ticket in tickets:
        profile = str(ticket.get("assigned_to") or "").strip()
        groups.setdefault(profile, []).append(ticket)

    ranked: list[tuple[float, float, float, int, str]] = []
    details: dict[str, dict[str, Any]] = {}
    for profile, items in groups.items():
        samples = len(items)
        if samples < minimum:
            continue
        successes = sum(1 for item in items if _first_pass(item))
        rate = successes / samples
        lower = wilson_lower(successes, samples)
        avg_attempts = round(
            sum(int(item.get("iterations") or 0) for item in items) / samples,
            2,
        )
        ranked.append((lower, rate, -avg_attempts, samples, profile))
        details[profile] = {
            "successes": successes,
            "samples": samples,
            "first_pass": round(rate, 3),
            "confidence": round(lower, 3),
            "avg_attempts": avg_attempts,
        }
    if not ranked:
        return None
    ranked.sort(reverse=True)
    _, _, _, samples, profile = ranked[0]
    stats = details[profile]
    variant, steps = _plan_for(kind, profile, config)
    return {
        "kind": kind,
        "profile": profile,
        "variant": variant,
        "steps": steps,
        "sample": samples,
        "first_pass": stats["first_pass"],
        "confidence": stats["confidence"],
        "avg_attempts": stats["avg_attempts"],
        "reason": (
            f"{stats['successes']}/{samples} cierres sin rework; "
            f"intentos medios {stats['avg_attempts']}"
        ),
    }


def _checklist_score(
    memory: dict[str, Any],
    ticket_kind: str | None,
    memory_kind: str | None,
    paths: list[str],
) -> tuple[int, list[str]]:
    """Puntúa un pitfall/convención según tipo de ticket, tipo de memoria y rutas."""
    if memory_kind and memory.get("kind") != memory_kind:
        return 0, []
    tagged = [str(item) for item in memory.get("ticket_kinds") or []]
    if ticket_kind and tagged and ticket_kind not in tagged:
        return 0, []
    memory_paths = [str(item) for item in memory.get("paths") or []]
    matched = [
        path
        for path in memory_paths
        if any(_paths_overlap(path, wanted) for wanted in paths)
    ]
    if paths and not matched:
        return 0, []
    reasons: list[str] = []
    score = 1
    if matched:
        score = 2
        reasons.append(f"ruta {matched[0]}")
    if ticket_kind:
        reasons.append(f"tipo de ticket {ticket_kind}")
    if memory_kind:
        reasons.append(f"memoria {memory_kind}")
    if not ticket_kind and not memory_kind and not paths:
        reasons.append("general")
    return score, reasons


def checklist(
    conn: sqlite3.Connection,
    ticket_kind: str | None = None,
    memory_kind: str | None = None,
    paths: Iterable[object] | None = None,
    config: dict[str, Any] | None = None,
    limit: int = CHECKLIST_LIMIT,
) -> list[dict[str, Any]]:
    """Pitfalls y convenciones activos que casan por tipo de ticket y rutas.

    ``ticket_kind`` filtra por el tipo del ticket (``KINDS``) usando la
    etiqueta ``ticket_kinds`` de la memoria: una memoria sin etiqueta es
    general y aplica a todos. ``memory_kind`` filtra por tipo de memoria
    (``pitfall`` o ``convention``); con ``paths`` solo se devuelven las
    entradas que tocan alguna ruta.
    """
    if ticket_kind:
        check_enum(ticket_kind, KINDS, "tipo de ticket")
    if memory_kind:
        check_enum(memory_kind, CHECKLIST_KINDS, "tipo de memoria")
    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 20:
        raise ValidationError("El límite del checklist debe estar entre 1 y 20")
    cfg = core.config(config)
    if not cfg["enabled"]:
        return []
    wanted = [str(item) for item in paths or [] if str(item).strip()]
    rows = list_memories(conn, status="active", limit=1000)
    ranked: list[tuple[int, int, float, str]] = []
    reasons: dict[str, list[str]] = {}
    for memory in rows:
        if memory.get("kind") not in CHECKLIST_KINDS:
            continue
        score, why = _checklist_score(memory, ticket_kind, memory_kind, wanted)
        if not score:
            continue
        ranked.append(
            (
                -score,
                0 if memory.get("pinned") or memory.get("locked") else 1,
                -float(memory.get("confidence") or 0.0),
                str(memory.get("code") or ""),
            )
        )
        reasons[str(memory.get("code") or "")] = why
    ranked.sort()
    selected: list[dict[str, Any]] = []
    for *_, code in ranked[:limit]:
        memory = get_memory(conn, code)
        summary = str(memory.get("description") or "").strip()
        if not summary:
            summary = " ".join(str(memory.get("content") or "").split())[:180]
        selected.append(
            {
                "code": memory.get("code"),
                "kind": memory.get("kind"),
                "ticket_kinds": list(memory.get("ticket_kinds") or []),
                "title": memory.get("title"),
                "summary": summary,
                "paths": list(memory.get("paths") or []),
                "evidence": [
                    {"kind": item["kind"], "ref": item["ref"]}
                    for item in (memory.get("evidence") or [])[:3]
                ],
                "confidence": memory.get("confidence"),
                "reason": "; ".join(reasons.get(str(memory.get("code")), [])),
            }
        )
    return selected


__all__ = [
    "CHECKLIST_KINDS",
    "CHECKLIST_LIMIT",
    "CLOSED_STATUSES",
    "checklist",
    "routing_advice",
]
