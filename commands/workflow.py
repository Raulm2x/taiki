"""Comandos de flujo de trabajo: transiciones, cierre y comentarios."""

from __future__ import annotations

import argparse
import sys

from ticketlib import repo, workspace, worktree

from .common import _conn, _read_body


def cmd_transition(args: argparse.Namespace) -> int:
    with _conn() as conn:
        ticket = repo.transition(
            conn,
            args.code,
            args.status,
            note=args.note,
            actor="usuario",
            actor_type="usuario",
        )
    print(f"{ticket['code']} → {ticket['status']}")
    return 0


def cmd_confirm(args: argparse.Namespace) -> int:
    note = args.note or "Confirmado por el usuario"
    with _conn() as conn:
        report = repo.confirm_tickets(conn, args.code, note=note)
        entries = []
        for code in args.code:
            try:
                ticket = repo.get_ticket(conn, code)
            except Exception:  # noqa: BLE001 - el error se informa abajo
                continue
            for link in ticket.get("links", []):
                if link["kind"] == "worktree":
                    entries.append(
                        {
                            "code": ticket["code"],
                            "path": link["ref"],
                            "branch": next(
                                (
                                    item["ref"]
                                    for item in ticket["links"]
                                    if item["kind"] == "rama"
                                ),
                                None,
                            ),
                        }
                    )
    for warning in worktree.warnings(workspace.project_root(), entries):
        print(f"aviso: {warning}", file=sys.stderr)

    for code in report["confirmed"]:
        print(f"{code} confirmado")
    for code in report["already"]:
        print(f"{code} ya estaba confirmado")

    if report["errors"]:
        print("No se confirmó ningún ticket:", file=sys.stderr)
        for code, detail in report["errors"].items():
            print(f"  {code}: {detail}", file=sys.stderr)
        return 1
    return 0


def cmd_reject(args: argparse.Namespace) -> int:
    with _conn() as conn:
        ticket = repo.transition(
            conn,
            args.code,
            "en_progreso",
            note=args.why,
            actor="usuario",
            actor_type="usuario",
        )
        repo.comment_ticket(
            conn,
            args.code,
            f"Rechazado en revisión: {args.why}",
            author="usuario",
        )
    iteration = int(ticket.get("iterations") or 0) + 1
    print(f"{ticket['code']} → en_progreso · siguiente iteración {iteration}")
    return 0


def cmd_reopen(args: argparse.Namespace) -> int:
    with _conn() as conn:
        ticket = repo.transition(
            conn,
            args.code,
            "en_progreso",
            note=args.why,
            actor="usuario",
            actor_type="usuario",
        )
    print(f"{ticket['code']} reabierto → en_progreso")
    return 0


def cmd_complete(args: argparse.Namespace) -> int:
    with _conn() as conn:
        ticket = repo.complete_ticket(
            conn,
            args.code,
            resolution=args.resolution,
            commit=args.commit,
            files=args.file,
            actor="usuario",
            actor_type="usuario",
        )
    print(f"{ticket['code']} completado")
    return 0


def cmd_comment(args: argparse.Namespace) -> int:
    body = " ".join(args.body).strip() or _read_body(None, "Comentario: ")
    with _conn() as conn:
        ticket = repo.comment_ticket(
            conn, args.code, body=body, author="usuario", actor_type="usuario"
        )
    print(f"Comentario añadido a {ticket['code']}")
    return 0
