"""Comando ``taiki brief``: brief operativo de la memoria."""

from __future__ import annotations

import argparse

from ticketlib import render, workspace
from ticketlib.memory.brief import render_brief, write_brief
from ticketlib.memory.config import load_memory_config

from .common import _conn


def cmd_brief(args: argparse.Namespace) -> int:
    config = load_memory_config()
    budget = (
        args.budget
        if args.budget is not None
        else int(config["brief_budget_chars"])
    )
    with _conn() as conn:
        if args.write:
            data = write_brief(
                workspace.project_root(),
                conn,
                config,
                budget=budget,
                section=args.section,
            )
        else:
            data = render_brief(
                conn, config, budget=budget, section=args.section
            )
    if args.json:
        print(render.to_json(data))
        return 0
    print(data["text"], end="")
    if args.write:
        print(f"\nEscrito: {data['path']}")
    return 0


__all__ = ["cmd_brief"]
