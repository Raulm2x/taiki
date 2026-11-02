"""Paquete de revisión para el curator: contexto compacto y seguro.

``review_packet`` reúne en una sola estructura los candidatos deterministas,
la memoria vigente, los últimos tickets cerrados o reabiertos, los umbrales,
las reglas activas y las instrucciones anti-aprendizaje. No escribe memoria,
no llama a un LLM y nunca incluye secretos ni transcripciones.
"""

from __future__ import annotations

import sqlite3
from typing import Any

from ..domain import ValidationError
from ..util import now
from .candidates import CANDIDATE_RULES, generate_candidates, rejection_reason
from .config import load_memory_config, validate_memory_config
from .security import SECRET_PATTERNS
from .store import list_memories

THRESHOLD_KEYS = (
    "candidate_min_evidence",
    "candidate_lookback_days",
    "candidate_routing_min_samples",
    "candidate_max",
    "candidate_similarity",
)

INSTRUCTIONS: tuple[str, ...] = (
    "Propón como máximo 5 memorias por revisión.",
    "Prefiere patch o supersede sobre add si la clave ya existe.",
    "Toda propuesta necesita evidencia verificable (ticket, commit, archivo o URL).",
    "No apruebes ni rechaces propuestas: esa decisión es del usuario.",
    "No edites código, configuración ni tickets desde la revisión.",
    "No guardes credenciales, transcripciones ni fallos transitorios.",
    "Si una regla no se repite o no tiene evidencia suficiente, no propongas nada.",
)

RECENT_STATUSES = ("completado", "verificado", "confirmado", "cancelado")
_SUMMARY_LIMIT = 300
_TITLE_LIMIT = 120


def _config(value: dict[str, Any] | None) -> dict[str, Any]:
    if value is None:
        return load_memory_config()
    if "memory" in value:
        return validate_memory_config(value.get("memory"))
    if "candidate_max" in value or "candidate_min_evidence" in value:
        return validate_memory_config(value)
    return load_memory_config(config=value)


def _scrub(text: object, limit: int = _SUMMARY_LIMIT) -> str:
    """Compacta un texto y redacta cualquier patrón con forma de secreto."""
    value = " ".join(str(text or "").split())
    for _, pattern in SECRET_PATTERNS:
        value = pattern.sub("[redactado]", value)
    return value[:limit]


def _compact_memory(memory: dict[str, Any]) -> dict[str, Any]:
    paths = [str(item) for item in (memory.get("paths") or []) if str(item).strip()]
    return {
        "code": str(memory.get("code") or ""),
        "scope": str(memory.get("scope") or "project"),
        "kind": str(memory.get("kind") or "fact"),
        "key": str(memory.get("key") or ""),
        "title": _scrub(memory.get("title"), _TITLE_LIMIT),
        "status": str(memory.get("status") or ""),
        "confidence": float(memory.get("confidence") or 0.0),
        "paths": paths[:5],
        "updated_at": str(memory.get("updated_at") or ""),
    }


def _latest_rejection(conn: sqlite3.Connection, ticket_id: int) -> str:
    rows = conn.execute(
        "SELECT body FROM comments WHERE ticket_id = ? AND author = 'usuario' "
        "ORDER BY id DESC",
        (ticket_id,),
    )
    for row in rows:
        reason = rejection_reason(row["body"])
        if reason is not None:
            return _scrub(reason)
    return ""


def _recent_tickets(
    conn: sqlite3.Connection, limit: int
) -> list[dict[str, Any]]:
    placeholders = ", ".join("?" for _ in RECENT_STATUSES)
    rows = conn.execute(
        "SELECT * FROM tickets WHERE archived = 0 AND "
        f"(status IN ({placeholders}) OR iterations >= 2) "
        "ORDER BY updated_at DESC, id DESC LIMIT ?",
        (*RECENT_STATUSES, limit),
    )
    out: list[dict[str, Any]] = []
    for row in rows:
        ticket = dict(row)
        out.append(
            {
                "code": str(ticket.get("code") or ""),
                "title": _scrub(ticket.get("title"), _TITLE_LIMIT),
                "kind": str(ticket.get("kind") or "otro"),
                "status": str(ticket.get("status") or ""),
                "iterations": int(ticket.get("iterations") or 0),
                "assigned_to": str(ticket.get("assigned_to") or ""),
                "resolution": _scrub(ticket.get("resolution")),
                "rejection": _latest_rejection(conn, int(ticket["id"])),
            }
        )
    return out


def review_packet(
    conn: sqlite3.Connection,
    config: dict[str, Any] | None = None,
    since: Any = None,
    limit: int | None = None,
) -> dict[str, Any]:
    """Construye el paquete completo para una sesión de revisión."""
    cfg = _config(config)
    maximum = int(limit if limit is not None else 20)
    if isinstance(limit, bool) or maximum <= 0:
        raise ValidationError("El límite de tickets recientes debe ser mayor que 0")

    candidates = generate_candidates(conn, cfg, since=since)
    current = [
        _compact_memory(memory)
        for memory in list_memories(conn, limit=100)
        if str(memory.get("status")) in ("active", "proposed", "stale")
    ]
    return {
        "generated_at": now(),
        "candidates": candidates,
        "current_memories": current,
        "recent_tickets": _recent_tickets(conn, maximum),
        "thresholds": {key: cfg[key] for key in THRESHOLD_KEYS},
        "rules": [dict(rule) for rule in CANDIDATE_RULES],
        "instructions": list(INSTRUCTIONS),
    }


__all__ = ["INSTRUCTIONS", "THRESHOLD_KEYS", "review_packet"]
