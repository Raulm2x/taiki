"""Comando ``taiki guard``: ¿hay ticket activo para esta acción?"""

from __future__ import annotations

import argparse
import json
import sys

from ticketlib import guard


def _hook() -> int:
    """Contrato genérico: JSON por stdin, decisión por stdout y exit 2 = deny."""
    try:
        payload = json.load(sys.stdin)
    except (json.JSONDecodeError, OSError) as exc:
        print(
            json.dumps(
                {"decision": "deny", "reason": f"JSON inválido: {exc}"},
                ensure_ascii=False,
            )
        )
        return 2
    data = guard.parse_hook_input(payload)
    result = guard.evaluate(
        tool=data["tool"],
        session=data["session"],
        agent=data["agent"],
        path=data["path"],
        command=data["command"],
    )
    print(
        json.dumps(
            {
                "decision": "allow" if result["allow"] else "deny",
                "action": result["action"],
                "ticket": result["ticket"],
                "reason": result["message"],
            },
            ensure_ascii=False,
        )
    )
    return 0 if result["allow"] else 2


def cmd_guard(args: argparse.Namespace) -> int:
    if args.hook:
        return _hook()
    # ``_conn`` no se usa aquí: el guard abre la DB solo si hace falta.
    result = guard.evaluate(
        tool=args.tool,
        session=args.session,
        agent=args.agent,
        path=args.path,
        command=args.command,
        cwd=args.cwd,
        auto=not args.no_auto,
    )
    if args.json:
        print(json.dumps(result, ensure_ascii=False))
    elif result["message"]:
        print(result["message"], file=sys.stdout if result["allow"] else sys.stderr)
    if result["allow"]:
        return 0
    return 3 if result["ticket"] else 4
