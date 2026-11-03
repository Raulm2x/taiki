"""Comandos de flujo de trabajo: transiciones, cierre y comentarios."""

from __future__ import annotations

import argparse
import sys

from ticketlib import repo, workspace, worktree
from ticketlib.store.workflow import authorize_ticket, review_ticket

from .common import _conn, _read_body


def cmd_authorize(args: argparse.Namespace) -> int:
    with _conn() as conn:
        ticket = authorize_ticket(
            conn,
            args.code,
            args.why,
            actor="usuario",
            actor_type="usuario",
        )
    print(
        f"{ticket['code']} autorizado: {args.why.strip()} "
        "(el guard aceptará comandos de alto riesgo)"
    )
    return 0


def cmd_review(args: argparse.Namespace) -> int:
    with _conn() as conn:
        ticket = review_ticket(
            conn,
            args.code,
            args.why,
            actor="usuario",
            actor_type="usuario",
        )
    print(
        f"{ticket['code']} revisado: {args.why.strip()} "
        "(ya puede marcarse como verificado)"
    )
    return 0


def cmd_transition(args: argparse.Namespace) -> int:
    if args.status == "confirmado":
        print(
            "Solo `taiki confirm` confirma tickets; "
            "`taiki transition` no puede saltarse esa regla.",
            file=sys.stderr,
        )
        return 1
    with _conn() as conn:
        ticket = repo.transition(
            conn,
            args.code,
            args.status,
            note=args.note,
            actor="usuario",
            actor_type="usuario",
            force=getattr(args, "force", False),
        )
    print(f"{ticket['code']} → {ticket['status']}")
    return 0


def cmd_confirm(args: argparse.Namespace) -> int:
    note = args.note or "Confirmado por el usuario"
    root = workspace.project_root()
    removed: list[dict] = []
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
        if getattr(args, "keep_worktrees", False) or not entries:
            kept = entries
        else:
            result = worktree.cleanup(root, entries)
            removed = result["removed"]
            kept = result["kept"]
            for item in removed:
                if item.get("code"):
                    conn.execute(
                        "DELETE FROM links WHERE kind IN ('worktree', 'rama') "
                        "AND ticket_id = (SELECT id FROM tickets WHERE code = ?)",
                        (item["code"],),
                    )
                conn.execute(
                    "DELETE FROM links WHERE kind IN ('worktree', 'rama') "
                    "AND ref = ?",
                    (str(item["path"]),),
                )
            conn.commit()
    for warning in worktree.warnings(root, kept):
        print(f"aviso: {warning}", file=sys.stderr)
    for item in removed:
        suffix = f" ({item['reason']})" if item.get("reason") else ""
        print(f"Worktree retirado: {item.get('code') or '?'} {item['path']}{suffix}")

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
    print(
        f"{ticket['code']} → en_progreso · intento {ticket['iterations']}"
    )
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
            force=getattr(args, "force", False),
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
