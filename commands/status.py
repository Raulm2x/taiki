"""Comando ``taiki status``: sesiones, claims, worktrees y solapamientos."""

from __future__ import annotations

import argparse
import sys

from ticketlib import render, status
from ticketlib.memory.config import load_memory_config
from ticketlib.memory.curate import auto_curate

from .common import _conn


def _maybe_auto_curate(conn) -> None:
    """Auto-curado opt-in: nunca rompe una lectura de status."""
    cfg = load_memory_config()
    if not cfg["auto_curate"]["enabled"]:
        return
    try:
        auto_curate(conn, cfg)
    except Exception as exc:  # noqa: BLE001 - status no debe fallar por memoria
        print(f"Aviso: auto-curado de memoria falló: {exc}", file=sys.stderr)


def cmd_status(args: argparse.Namespace) -> int:
    with _conn() as conn:
        _maybe_auto_curate(conn)
    data = status.snapshot(
        window=args.window, with_servers=not args.no_servers
    )
    if args.json:
        print(render.to_json(data))
        return 0
    rows = status.session_lines(data) if args.sessions else status.lines(data)
    for line in rows:
        print(line)
    return 0
