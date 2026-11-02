"""Herramientas MCP de memoria: brief, búsqueda, escritura e hints de flujo."""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

from ticketlib import domain, workspace
from ticketlib.memory.brief import render_brief, write_brief
from ticketlib.memory.config import load_memory_config
from ticketlib.memory.lifecycle import (
    archive_memory,
    feedback_memory,
    patch_memory,
    pin_memory,
    supersede_memory,
)
from ticketlib.memory.store import (
    add_memory,
    get_memory,
    list_memories,
    search_memories,
)
from ticketlib.util import now

SNIPPET_CHARS = 240
HINT_LIMIT = 3
_ACTIVE_STATUSES = ("active", "stale")


def _config() -> dict[str, Any]:
    info = workspace.resolve()
    if info.get("workspace"):
        return load_memory_config(info["workspace"])
    return load_memory_config()


def _project_root() -> Path:
    info = workspace.resolve()
    if info.get("workspace"):
        return Path(info["workspace"]).parent
    return workspace.project_root()


def _paths_overlap(left: str, right: str) -> bool:
    a = str(left or "").strip().strip("*").rstrip("/")
    b = str(right or "").strip().strip("*").rstrip("/")
    if not a or not b:
        return False
    return a == b or a.startswith(b + "/") or b.startswith(a + "/")


def hints(
    conn: sqlite3.Connection,
    ticket: dict[str, Any],
    *,
    limit: int = HINT_LIMIT,
) -> list[dict[str, Any]]:
    """Memorias relevantes por rutas y tipo del ticket, sin contenido completo."""
    cfg = _config()
    if not cfg["enabled"]:
        return []
    ticket_paths = [str(item) for item in ticket.get("paths") or []]
    ticket_kind = str(ticket.get("kind") or "")
    tags = [str(item).lower() for item in ticket.get("tags") or []]
    rows = list_memories(conn, limit=200)
    ranked: list[tuple[int, dict[str, Any]]] = []
    for memory in rows:
        if memory.get("status") not in _ACTIVE_STATUSES:
            continue
        score = 0
        reasons: list[str] = []
        memory_paths = [str(item) for item in memory.get("paths") or []]
        matches = [
            path
            for path in memory_paths
            if any(_paths_overlap(path, wanted) for wanted in ticket_paths)
        ]
        if matches:
            score += 10
            reasons.append(f"ruta {matches[0]}")
        kinds = {
            str(memory.get("kind") or ""),
            *[str(item) for item in memory.get("kinds") or []],
        }
        if ticket_kind and ticket_kind in kinds:
            score += 6
            reasons.append(f"tipo {ticket_kind}")
        haystack = " ".join(
            str(memory.get(key) or "")
            for key in ("title", "description", "content", "key")
        ).lower()
        for tag in tags:
            if tag and tag in haystack:
                score += 2
                reasons.append(f"etiqueta {tag}")
                break
        if not score:
            continue
        if memory.get("scope") == "project":
            score += 1
        ranked.append(
            (
                score,
                {
                    "code": memory.get("code"),
                    "title": memory.get("title"),
                    "kind": memory.get("kind"),
                    "reason": "; ".join(reasons),
                },
            )
        )
    ranked.sort(key=lambda item: (-item[0], str(item[1]["code"])))
    return [item for _, item in ranked[:limit]]


def learning(ticket: dict[str, Any]) -> dict[str, Any] | None:
    """Nudge para proponer una lección tras rework o reapertura."""
    iterations = int(ticket.get("iterations") or 0)
    reopened = any(
        event.get("from_status") in ("verificado", "completado")
        and event.get("to_status") == "en_progreso"
        for event in ticket.get("events") or []
    )
    if not reopened and iterations <= 1:
        return None
    if reopened:
        reason = "El ticket se reabrió desde verificado/completado; hubo corrección o rework."
    else:
        reason = f"El ticket acumula {iterations} iteraciones; revisa si hay una lección."
    evidence: list[dict[str, str]] = [
        {"kind": "ticket", "ref": str(ticket.get("code") or "")}
    ]
    for commit in ticket.get("commits") or []:
        evidence.append({"kind": "commit", "ref": str(commit)})
    return {"suggested": True, "reason": reason, "evidence": evidence}


def _snippet(memory: dict[str, Any]) -> dict[str, Any]:
    data = {
        key: memory.get(key)
        for key in (
            "code",
            "scope",
            "kind",
            "kinds",
            "key",
            "title",
            "description",
            "status",
            "confidence",
            "paths",
            "pinned",
            "use_count",
            "updated_at",
        )
        if memory.get(key) not in (None, "", [], {})
    }
    content = str(memory.get("content") or "")
    data["snippet"] = content[:SNIPPET_CHARS] + (
        "…" if len(content) > SNIPPET_CHARS else ""
    )
    return data


def _matches(memory: dict[str, Any], paths: list[str], kinds: list[str]) -> bool:
    if paths and not any(
        _paths_overlap(path, str(wanted))
        for path in memory.get("paths") or []
        for wanted in paths
    ):
        return False
    if kinds:
        available = {
            str(memory.get("kind") or ""),
            *[str(item) for item in memory.get("kinds") or []],
        }
        if not available & set(kinds):
            return False
    return True


def handle_brief(
    conn: sqlite3.Connection,
    args: dict[str, Any],
    _session: str | None = None,
    _actor: str = "agente",
) -> dict[str, Any]:
    cfg = _config()
    section = str(args.get("section") or "all")
    budget = args.get("budget")
    if args.get("write"):
        result = write_brief(
            _project_root(), conn, cfg, budget=budget, section=section
        )
    else:
        result = render_brief(conn, cfg, budget, section=section)
    result["generated_at"] = now()
    return result


def handle_memory_search(
    conn: sqlite3.Connection,
    args: dict[str, Any],
    session: str | None = None,
    _actor: str = "agente",
) -> dict[str, Any]:
    query = str(args.get("query") or "").strip()
    limit = int(args.get("limit") or 10)
    scope = args.get("scope")
    status = args.get("status")
    kind = args.get("kind")
    paths = [str(item) for item in args.get("paths") or []]
    kinds = [str(item) for item in args.get("kinds") or []]
    includes_inactive = status in ("archived", "superseded")
    fetch = max(limit * 5, 100)
    if query:
        rows = search_memories(
            conn,
            query,
            scope=scope,
            status=status,
            limit=fetch,
            session_id=session,
        )
    else:
        rows = list_memories(
            conn,
            scope=scope,
            status=status,
            kind=kind,
            limit=fetch,
            include_inactive=includes_inactive,
        )
    rows = [row for row in rows if _matches(row, paths, kinds)]
    total = len(rows)
    selected = rows[:limit]
    if args.get("include_content"):
        memories = selected
    else:
        memories = [_snippet(row) for row in selected]
    return {"memories": memories, "count": len(memories), "total": total}


def _operation_receipt(
    conn: sqlite3.Connection,
    op: dict[str, Any],
    *,
    target: str,
    session: str | None,
    actor: str,
) -> dict[str, Any]:
    action = str(op.get("action") or "")
    code = op.get("code")
    reason = op.get("reason")
    common = {"actor": actor, "session_id": session, "reason": reason}
    if action == "add":
        return add_memory(
            conn,
            title=op.get("title") or "",
            content=op.get("content") or "",
            kind=op.get("kind") or "fact",
            key=op.get("key"),
            scope=target,
            confidence=op.get("confidence", 0.5),
            source="agent",
            evidence=op.get("evidence"),
            pinned=bool(op.get("pin")),
            **common,
        )
    if not code:
        raise domain.ValidationError(
            f"La operación {action!r} requiere el campo 'code'"
        )
    if action == "patch":
        fields = {
            key: op[key]
            for key in ("title", "content", "kind", "key", "confidence")
            if key in op
        }
        receipt = patch_memory(conn, code, **fields, **common)
        if "pin" in op:
            receipt = pin_memory(
                conn,
                code,
                pinned=bool(op.get("pin")),
                actor=actor,
                session_id=session,
                reason=reason,
            )
        return receipt
    if action == "remove":
        return archive_memory(conn, code, **common)
    if action == "feedback":
        return feedback_memory(
            conn,
            code,
            helpful=bool(op.get("helpful", True)),
            note=reason,
            actor=actor,
            session_id=session,
        )
    if action == "supersede":
        old = get_memory(conn, code)
        receipt = add_memory(
            conn,
            title=op.get("title") or old["title"],
            content=op.get("content") or old["content"],
            kind=op.get("kind") or old["kind"],
            key=op.get("key") or old["key"],
            scope=old["scope"],
            confidence=op.get("confidence", old.get("confidence", 0.5)),
            source="agent",
            evidence=op.get("evidence"),
            pinned=bool(op.get("pin", old.get("pinned"))),
            **common,
        )
        current = get_memory(conn, code)
        if current["status"] != "superseded":
            supersede_memory(
                conn,
                code,
                receipt["code"],
                actor=actor,
                session_id=session,
                reason=reason,
            )
        return receipt
    raise domain.ValidationError(f"Acción de memoria desconocida: {action!r}")


def handle_memory_write(
    conn: sqlite3.Connection,
    args: dict[str, Any],
    session: str | None = None,
    actor: str = "agente",
) -> dict[str, Any]:
    operations = args.get("operations") or []
    if not operations:
        raise domain.ValidationError("memory_write necesita al menos una operación")
    target = str(args.get("target") or "project")
    applied = 0
    proposed = 0
    ids: list[str] = []
    warnings: list[str] = []
    budgets: dict[str, Any] = {}
    for op in operations:
        receipt = _operation_receipt(
            conn, op, target=target, session=session, actor=actor
        )
        code = str(receipt.get("code") or "")
        if code:
            ids.append(code)
        if receipt.get("status") == "proposed":
            proposed += 1
        else:
            applied += 1
        scope = str(receipt.get("scope") or target)
        if receipt.get("budget"):
            budgets[scope] = receipt["budget"]
        warnings.extend(str(item) for item in receipt.get("warnings") or [])
    return {
        "applied": applied,
        "proposed": proposed,
        "ids": ids,
        "budget": budgets,
        "warnings": list(dict.fromkeys(warnings)),
    }


HANDLERS = {
    "brief": handle_brief,
    "memory_search": handle_memory_search,
    "memory_write": handle_memory_write,
}


__all__ = ["HANDLERS", "hints", "learning"]
