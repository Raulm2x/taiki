"""Comandos ``taiki memory`` y el atajo ``taiki remember``.

El despacho público vive aquí; cada subcomando se implementa en un módulo
hermano por dominio (consulta, escritura, transferencia y playbooks).
"""

from __future__ import annotations

import argparse

from ticketlib import workspace  # los tests parchean project_root desde aquí
from ticketlib.domain import ValidationError

from .common import _conn
from .memory_playbook import _playbook
from .memory_query import (
    _candidates,
    _curate,
    _impact,
    _list,
    _pending,
    _review,
    _show,
    _stats,
)
from .memory_transfer import _export, _import, _rollback
from .memory_write import (
    _add,
    _approve,
    _edit,
    _pin,
    _reject,
    _rm,
    cmd_remember,
)


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
        if action == "candidates":
            return _candidates(conn, args)
        if action == "review":
            return _review(conn, args)
        if action == "curate":
            return _curate(conn, args)
        if action == "stats":
            return _stats(conn, args)
        if action == "impact":
            return _impact(conn, args)
        if action == "export":
            return _export(conn, args)
        if action == "import":
            return _import(conn, args)
        if action == "rollback":
            return _rollback(conn, args)
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
        if action == "playbook":
            return _playbook(conn, args)
    raise ValidationError(f"Ación de memoria desconocida: {action}")


__all__ = ["cmd_memory", "cmd_remember"]
