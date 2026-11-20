"""Paquete de revisión para el curator: contexto compacto y seguro.

``review_packet`` reúne en una sola estructura los candidatos deterministas,
la memoria vigente, los últimos tickets cerrados o reabiertos, los umbrales,
las reglas activas y las instrucciones anti-aprendizaje. No escribe memoria,
no llama a un LLM y nunca incluye secretos ni transcripciones.
"""

from __future__ import annotations

import json
import sqlite3
import subprocess
from typing import Any

from .. import db
from ..domain import ValidationError
from ..util import now
from .candidates import CANDIDATE_RULES, generate_candidates, rejection_reason
from .config import resolve_memory_config
from .models import default_key
from .security import SECRET_PATTERNS
from .store import add_memory, list_memories

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
    return resolve_memory_config(value)


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


def _review_operations(payload: Any) -> list[dict[str, Any]]:
    operations = payload.get("operations") if isinstance(payload, dict) else payload
    if not isinstance(operations, list):
        raise ValidationError(
            "review_command debe devolver una lista de operaciones o "
            "{\"operations\": [...]}"
        )
    if len(operations) > 5:
        raise ValidationError("review_command no puede proponer más de 5 memorias")
    if not all(isinstance(item, dict) for item in operations):
        raise ValidationError("Cada operación de review_command debe ser un objeto")
    return operations


def run_review_command(
    conn: sqlite3.Connection,
    config: dict[str, Any] | None = None,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Ejecuta el comando externo opt-in y deja sus salidas como propuestas.

    El comando recibe el review packet por stdin y devuelve JSON por stdout. El
    núcleo no usa red ni claves: solo valida la salida y crea propuestas que el
    usuario debe aprobar. Sin ``review_command`` no hace nada.
    """
    cfg = _config(config)
    command = cfg["review_command"]
    if not command:
        return {
            "configured": False,
            "command": None,
            "operations": 0,
            "proposed": [],
            "dry_run": bool(dry_run),
        }
    packet = review_packet(conn, cfg)
    text = json.dumps(packet, ensure_ascii=False, sort_keys=True)
    timeout = int(cfg["review_timeout_seconds"])
    try:
        completed = subprocess.run(
            str(command),
            shell=True,
            input=text,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise ValidationError(
            f"review_command agotó el tiempo de espera ({timeout}s)"
        ) from exc
    except OSError as exc:
        raise ValidationError(f"No se pudo ejecutar review_command: {exc}") from exc
    if completed.returncode != 0:
        detail = _scrub(completed.stderr or completed.stdout, 500)
        raise ValidationError(
            f"review_command falló con código {completed.returncode}: {detail}"
        )
    try:
        payload = json.loads(completed.stdout or "[]")
    except json.JSONDecodeError as exc:
        raise ValidationError(
            f"review_command devolvió JSON inválido: {exc}"
        ) from exc
    operations = _review_operations(payload)

    if dry_run:
        conn.execute("SAVEPOINT review_command_dry_run")
    proposed: list[str] = []
    try:
        with db.tx(conn):
            for operation in operations:
                action = str(operation.get("action") or "add")
                if action != "add":
                    raise ValidationError(
                        "review_command solo puede proponer operaciones add; "
                        f"recibido {action!r}"
                    )
                title = str(operation.get("title") or "")
                content = str(operation.get("content") or "")
                key = operation.get("key") or default_key(title)
                scope = str(operation.get("scope") or "project")
                existing = conn.execute(
                    'SELECT code FROM memories WHERE scope = ? AND "key" = ? '
                    "AND status IN ('active', 'proposed', 'stale') LIMIT 1",
                    (scope, key),
                ).fetchone()
                if existing is not None:
                    raise ValidationError(
                        f"review_command intenta reemplazar {existing['code']} "
                        f"con la clave {key!r}; usa una propuesta manual"
                    )
                receipt = add_memory(
                    conn,
                    title=title,
                    content=content,
                    kind=str(operation.get("kind") or "fact"),
                    key=key,
                    scope=scope,
                    description=operation.get("description") or "",
                    paths=operation.get("paths") or [],
                    kinds=operation.get("kinds") or [],
                    ticket_kinds=operation.get("ticket_kinds") or [],
                    confidence=operation.get("confidence", 0.5),
                    source="import",
                    status="proposed",
                    actor="review_command",
                    evidence=operation.get("evidence") or [],
                    files=operation.get("files") or [],
                    reason="propuesta de review_command",
                )
                proposed.append(str(receipt["code"]))
    finally:
        if dry_run:
            conn.execute("ROLLBACK TO review_command_dry_run")
            conn.execute("RELEASE review_command_dry_run")
    return {
        "configured": True,
        "command": str(command),
        "operations": len(operations),
        "proposed": proposed,
        "dry_run": bool(dry_run),
    }


__all__ = [
    "INSTRUCTIONS",
    "THRESHOLD_KEYS",
    "review_packet",
    "run_review_command",
]
