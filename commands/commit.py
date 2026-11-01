"""Comandos ``taiki commit`` y ``taiki hook-check-index``."""

from __future__ import annotations

import argparse
import json
import sys
from typing import Any

from ticketlib import commits, db, env, render, repo, workspace

from .common import _conn

DISABLED_VALUES = {"0", "off", "false", "no", "disabled"}


def _active(conn, exclude: str | None = None) -> list[dict[str, Any]]:
    rows = conn.execute(
        "SELECT code, paths FROM tickets "
        "WHERE archived = 0 AND status = 'en_progreso' ORDER BY updated_at DESC"
    ).fetchall()
    result = []
    for row in rows:
        if exclude and row["code"] == exclude:
            continue
        raw = row["paths"]
        try:
            paths = json.loads(raw) if raw else []
        except (TypeError, json.JSONDecodeError):
            paths = [raw] if raw else []
        result.append({"code": row["code"], "paths": paths})
    return result


def _resolve(conn, explicit: str | None, *, quiet: bool = False) -> dict[str, Any] | None:
    code = (explicit or env.get("TICKET") or "").strip()
    if code:
        return repo.get_ticket(conn, code)
    active = [
        row["code"]
        for row in conn.execute(
            "SELECT code FROM tickets WHERE archived = 0 "
            "AND status = 'en_progreso' ORDER BY updated_at DESC"
        )
    ]
    if len(active) == 1:
        return repo.get_ticket(conn, active[0])
    if not quiet:
        if active:
            print(
                "Hay varios tickets activos: usa --ticket <código> "
                f"({', '.join(active)})",
                file=sys.stderr,
            )
        else:
            print("No hay ticket activo: reclama uno antes de commitear", file=sys.stderr)
    return None


def _with_code(message: str, code: str) -> str:
    lines = (message or "").strip().splitlines() or [""]
    if code not in lines[0]:
        lines[0] = f"{lines[0]} ({code})".strip()
    return "\n".join(lines)


def cmd_commit(args: argparse.Namespace) -> int:
    root = workspace.checkout_root()
    with _conn() as conn:
        ticket = _resolve(conn, args.ticket)
        if ticket is None:
            return 1
        report = commits.check(root, ticket, _active(conn, ticket["code"]))
        if not report["staged"]:
            print("Nada en el índice: usa `git add <ruta>` primero", file=sys.stderr)
            return 1
        if args.json and args.dry_run:
            print(render.to_json(report))
            return 0 if report["ok"] else 1
        if report["outside"]:
            print(
                f"Fuera del alcance de {ticket['code']} "
                f"({', '.join(report['scope'])}):",
                file=sys.stderr,
            )
            for item in report["outside"]:
                print(f"  {item}", file=sys.stderr)
        for conflict in report["conflicts"]:
            print(
                f"Solapa con {conflict['ticket']}: "
                f"{', '.join(conflict['files'] or conflict['overlap'])}",
                file=sys.stderr,
            )
        if not report["ok"]:
            print(
                "Commit cancelado. Stagea solo tus rutas o ajusta "
                f"`taiki update {ticket['code']} --paths ...`.",
                file=sys.stderr,
            )
            return 1
        if args.dry_run:
            print(f"{ticket['code']}: {len(report['staged'])} fichero(s) listos")
            for item in report["staged"]:
                print(f"  {item}")
            return 0
        message = args.message or ticket["title"]
        message = _with_code(message, ticket["code"])
        sha = commits.commit(root, message)
        repo.link_ticket(conn, ticket["code"], "commit", sha)
    leftover = commits.unstaged(root)
    if leftover:
        print(
            f"aviso: quedan {len(leftover)} fichero(s) sin stagear; "
            "no se tocaron.",
            file=sys.stderr,
        )
    print(f"{ticket['code']} → commit {sha}")
    return 0


def cmd_hook_check_index(args: argparse.Namespace) -> int:
    if str(env.get("HOOK", "")).strip().lower() in DISABLED_VALUES:
        return 0
    root = workspace.checkout_root()
    try:
        conn = db.connect()
    except Exception:  # noqa: BLE001 - sin DB no se bloquea
        return 0
    try:
        explicit = (args.ticket or env.get("TICKET") or "").strip()
        if explicit:
            ticket = repo.get_ticket(conn, explicit)
        else:
            active = [
                row["code"]
                for row in conn.execute(
                    "SELECT code FROM tickets WHERE archived = 0 "
                    "AND status = 'en_progreso' ORDER BY updated_at DESC"
                )
            ]
            if len(active) > 1:
                print(
                    "pre-commit: hay varios tickets activos "
                    f"({', '.join(active)}); define TAIKI_TICKET o usa "
                    "`taiki commit --ticket`.",
                    file=sys.stderr,
                )
                return 1
            if not active:
                return 0
            ticket = repo.get_ticket(conn, active[0])
        report = commits.check(root, ticket, _active(conn, ticket["code"]))
        if report["outside"]:
            print(
                f"pre-commit: rutas fuera de {ticket['code']}:",
                file=sys.stderr,
            )
            for item in report["outside"]:
                print(f"  {item}", file=sys.stderr)
        for conflict in report["conflicts"]:
            print(
                f"pre-commit: {ticket['code']} solapa con "
                f"{conflict['ticket']} en {', '.join(conflict['files'] or conflict['overlap'])}",
                file=sys.stderr,
            )
        return 0 if report["ok"] else 1
    finally:
        conn.close()
