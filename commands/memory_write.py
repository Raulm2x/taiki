"""Subcomandos de escritura y aprobación de memoria."""

from __future__ import annotations

import argparse

from ticketlib import render
from ticketlib.domain import ValidationError
from ticketlib.memory import (
    add_memory,
    approve_memory,
    archive_memory,
    patch_memory,
    pin_memory,
    reject_memory,
)

from .common import _conn
from .memory_common import _as_json, _print_memory, _require_user, _session

_EDIT_FIELDS = (
    "title",
    "content",
    "description",
    "key",
    "kind",
    "confidence",
    "ticket_kinds",
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
        ticket_kinds=args.ticket_kinds,
        tags=getattr(args, "tags", None),
        pinned=bool(args.pin),
    )
    _print_memory(memory, _as_json(args))
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
    if getattr(args, "tags", None) is not None:
        updates["tags"] = args.tags
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
