"""Comando ``taiki status``: sesiones, claims, worktrees y solapamientos."""

from __future__ import annotations

import argparse

from ticketlib import render, status


def cmd_status(args: argparse.Namespace) -> int:
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
