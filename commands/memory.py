"""Comandos ``taiki memory`` y el atajo ``taiki remember``."""

from __future__ import annotations

import argparse
import os
from typing import Any

from ticketlib import render
from ticketlib.domain import ValidationError
from ticketlib.memory import (
    add_memory,
    approve_memory,
    archive_memory,
    get_memory,
    list_memories,
    patch_memory,
    pin_memory,
    reject_memory,
)
from ticketlib.memory import budget as memory_budget
from ticketlib.memory import core as memory_core
from ticketlib.memory.render import memory_summary

from .common import _conn

_EDIT_FIELDS = ("title", "content", "description", "key", "kind", "confidence")


def _session() -> str | None:
    return (
        os.environ.get("OPENCODE_SESSION_ID")
        or os.environ.get("AGY_SESSION_ID")
        or os.environ.get("SESSION_ID")
        or None
    )


def _as_json(args: argparse.Namespace) -> bool:
    return bool(getattr(args, "json", False))


def _print_memory(memory: dict[str, Any], as_json: bool) -> None:
    if as_json:
        print(render.to_json(memory))
        return
    print(
        f"{memory['code']}  {memory['status']}  "
        f"{memory['scope']}/{memory['kind']}  {memory['title']}"
    )
    if memory.get("content"):
        print(f"  {memory['content']}")
    evidence = memory.get("evidence") or []
    if evidence:
        print(
            "  Evidencia: "
            + ", ".join(f"{item['kind']}:{item['ref']}" for item in evidence)
        )
    for warning in memory.get("warnings") or []:
        print(f"  Aviso: {warning}")


def _require_user(actor: str | None) -> None:
    if str(actor or "").strip().lower() != "usuario":
        raise ValidationError(
            "Solo el usuario puede aprobar o rechazar memorias (actor 'usuario')"
        )


def _add(conn, args: argparse.Namespace) -> int:
    memory = add_memory(
        conn,
        title=args.title,
        content=args.content,
        kind=args.kind,
        key=args.key,
        scope=args.scope,
        confidence=args.confidence,
        source="user",
        actor="usuario",
        session_id=_session(),
        evidence=list(args.evidence or []),
        pinned=bool(args.pin),
    )
    _print_memory(memory, _as_json(args))
    return 0


def _list(conn, args: argparse.Namespace) -> int:
    rows = list_memories(
        conn,
        scope=args.scope,
        status=args.status,
        kind=args.kind,
        limit=args.limit,
        include_inactive=args.status in ("archived", "superseded"),
    )
    if _as_json(args):
        print(render.to_json({"count": len(rows), "memories": rows}))
        return 0
    if not rows:
        print("No hay memorias con esos filtros.")
        return 0
    for memory in rows:
        print(
            f"{memory_summary(memory)}  ({memory_budget.entry_chars(memory)} chars)"
        )
    return 0


def _show(conn, args: argparse.Namespace) -> int:
    memory = get_memory(
        conn,
        args.code,
        session_id=_session(),
        record_usage=True,
    )
    memory["ledger"] = [
        dict(row)
        for row in conn.execute(
            "SELECT id, ts, actor, action, reason FROM memory_ledger "
            "WHERE memory_id = ? ORDER BY id DESC LIMIT 20",
            (memory["id"],),
        )
    ]
    memory["usage"] = [
        dict(row)
        for row in conn.execute(
            "SELECT ts, action, session_id, helpful, note FROM memory_usage "
            "WHERE memory_id = ? ORDER BY id DESC LIMIT 20",
            (memory["id"],),
        )
    ]
    cfg = memory_core.config(None)
    entries = memory_core.scope_entries(conn, memory["scope"])
    memory["budget"] = memory_budget.summary(
        entries, memory_core.limit_for(cfg, memory["scope"]), memory["scope"]
    )
    if _as_json(args):
        print(render.to_json(memory))
        return 0
    _print_memory(memory, False)
    files = memory.get("files") or []
    if files:
        print("  Ficheros: " + ", ".join(item["path"] for item in files))
    print(
        f"  Historial: {len(memory['ledger'])} asiento(s) · "
        f"uso: {len(memory['usage'])} registro(s) · "
        f"presupuesto: {memory['budget']['used']}/{memory['budget']['limit']}"
    )
    return 0


def _pending(conn, args: argparse.Namespace) -> int:
    rows = list_memories(conn, status="proposed", limit=args.limit)
    if _as_json(args):
        print(render.to_json({"count": len(rows), "memories": rows}))
        return 0
    if not rows:
        print("No hay propuestas pendientes.")
        return 0
    for memory in rows:
        print(memory_summary(memory))
        if memory.get("content"):
            print(f"  {memory['content']}")
    return 0


def _approve(conn, args: argparse.Namespace) -> int:
    _require_user(args.actor)
    memory = approve_memory(
        conn,
        args.code,
        actor="usuario",
        session_id=_session(),
        reason=args.why,
    )
    _print_memory(memory, _as_json(args))
    return 0


def _reject(conn, args: argparse.Namespace) -> int:
    _require_user(args.actor)
    memory = reject_memory(
        conn,
        args.code,
        actor="usuario",
        session_id=_session(),
        reason=args.why or "rechazada",
    )
    _print_memory(memory, _as_json(args))
    return 0


def _pin(conn, args: argparse.Namespace, value: bool) -> int:
    memory = pin_memory(
        conn,
        args.code,
        pinned=value,
        actor="usuario",
        session_id=_session(),
    )
    _print_memory(memory, _as_json(args))
    return 0


def _rm(conn, args: argparse.Namespace) -> int:
    memory = archive_memory(
        conn,
        args.code,
        actor="usuario",
        session_id=_session(),
        reason=args.why,
    )
    if _as_json(args):
        print(render.to_json(memory))
    else:
        print(
            f"{memory['code']} archivada (no borrada). "
            "El histórico queda en el ledger."
        )
    return 0


def _edit(conn, args: argparse.Namespace) -> int:
    updates = {
        name: getattr(args, name)
        for name in _EDIT_FIELDS
        if getattr(args, name) is not None
    }
    if args.paths is not None:
        updates["paths"] = args.paths
    if not updates:
        raise ValidationError("Indica al menos un campo para editar")
    memory = patch_memory(
        conn,
        args.code,
        actor="usuario",
        session_id=_session(),
        reason=args.why,
        **updates,
    )
    _print_memory(memory, _as_json(args))
    return 0


def cmd_memory(args: argparse.Namespace) -> int:
    """Despacha los subcomandos de ``taiki memory``."""
    action = args.memory_action
    with _conn() as conn:
        if action == "add":
            return _add(conn, args)
        if action == "list":
            return _list(conn, args)
        if action == "show":
            return _show(conn, args)
        if action == "pending":
            return _pending(conn, args)
        if action == "approve":
            return _approve(conn, args)
        if action == "reject":
            return _reject(conn, args)
        if action == "pin":
            return _pin(conn, args, True)
        if action == "unpin":
            return _pin(conn, args, False)
        if action == "rm":
            return _rm(conn, args)
        if action == "edit":
            return _edit(conn, args)
    raise ValidationError(f"Acción de memoria desconocida: {action}")


def cmd_remember(args: argparse.Namespace) -> int:
    """Atajo para una preferencia activa del usuario."""
    text = " ".join(args.text).strip()
    if not text:
        raise ValidationError("El texto de remember no puede estar vacío")
    title = args.title or text[:80]
    if args.ticket:
        evidence = [{"kind": "ticket", "ref": args.ticket}]
    else:
        evidence = [{"kind": "session", "ref": _session() or "ses_cli"}]
    with _conn() as conn:
        memory = add_memory(
            conn,
            title=title,
            content=text,
            kind="preference",
            scope="user",
            source="user",
            actor="usuario",
            session_id=_session(),
            evidence=evidence,
        )
    _print_memory(memory, _as_json(args))
    return 0


__all__ = ["cmd_memory", "cmd_remember"]
