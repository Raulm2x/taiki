"""Comandos ``taiki worktree``: aislamiento por tarea con DB compartida."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from ticketlib import render, repo, workspace, worktree
from ticketlib.domain import TicketError

from .common import _conn


def _root() -> Path:
    return workspace.project_root()


def _open_ticket(conn, code: str) -> dict:
    ticket = repo.get_ticket(conn, code)
    if ticket["status"] in ("completado", "confirmado", "cancelado"):
        raise SystemExit(
            f"{ticket['code']} está '{ticket['status']}'; reábrelo antes de "
            "crear un worktree"
        )
    return ticket


def cmd_worktree_create(args: argparse.Namespace) -> int:
    root = _root()
    with _conn() as conn:
        ticket = _open_ticket(conn, args.code)
        existing = [
            link["ref"]
            for link in ticket.get("links", [])
            if link["kind"] == "worktree"
        ]
        if existing and not args.force:
            print(
                f"{ticket['code']} ya tiene worktree: {existing[0]} "
                "(usa --force para crear otro)",
                file=sys.stderr,
            )
            return 1
        result = worktree.create(
            root,
            ticket["code"],
            title=ticket["title"],
            branch=args.branch,
            path=args.path,
            start=args.start,
        )
        repo.link_ticket(conn, ticket["code"], "worktree", str(result["path"]))
        repo.link_ticket(conn, ticket["code"], "rama", result["branch"])
    if args.json:
        print(render.to_json(result))
        return 0
    print(f"Worktree {ticket['code']}: {result['path']}")
    print(f"Rama: {result['branch']}")
    if result["bootstrap"]:
        print(f"Config copiada: {', '.join(result['bootstrap'])}")
    print("La base de datos sigue siendo la del checkout principal.")
    print(f"  cd {result['path']} && opencode")
    return 0


def cmd_worktree_list(args: argparse.Namespace) -> int:
    root = _root()
    with _conn() as conn:
        rows = conn.execute(
            "SELECT t.code AS code, l.ref AS path "
            "FROM links l JOIN tickets t ON t.id = l.ticket_id "
            "WHERE l.kind = 'worktree' ORDER BY l.id DESC"
        ).fetchall()
    by_path = {str(Path(row["path"]).expanduser()): row["code"] for row in rows}
    items = []
    for item in worktree.list_worktrees(root):
        path = Path(item["path"])
        if path.resolve() == root.resolve():
            continue
        state = worktree.status(path)
        items.append(
            {
                "code": by_path.get(str(path)) or by_path.get(str(path.resolve())),
                "path": str(path),
                "branch": item.get("branch"),
                "dirty": state["dirty"],
                "files": state["files"],
                "merged": worktree.merged(root, item.get("branch") or ""),
            }
        )
    if args.json:
        print(render.to_json({"worktrees": items}))
        return 0
    if not items:
        print("No hay worktrees de tareas.")
        return 0
    print(f"{'TICKET':<10} {'RAMA':<28} {'ESTADO':<18} RUTA")
    for item in items:
        state = "sin commitear" if item["dirty"] else (
            "sin mergear" if not item["merged"] else "mergeada"
        )
        print(
            f"{item['code'] or '?':<10} {item['branch'] or '?':<28} "
            f"{state:<18} {item['path']}"
        )
    return 0


def cmd_worktree_remove(args: argparse.Namespace) -> int:
    root = _root()
    target = args.path
    code = args.code
    if target is None:
        if not code:
            raise SystemExit("Indica el ticket o --path")
        found = worktree.locate(root, code)
        if found is None:
            raise SystemExit(f"No se encontró worktree para {code}")
        target = found["path"]
    state = worktree.status(target)
    if state["dirty"] and not args.force:
        print(
            f"{target} tiene cambios sin commitear; usa --force para descartarlos",
            file=sys.stderr,
        )
        return 1
    removed = worktree.remove(
        root,
        target,
        force=args.force,
        delete_branch=args.delete_branch,
    )
    with _conn() as conn:
        refs = [str(Path(target).expanduser())]
        if removed.get("branch"):
            refs.append(str(removed["branch"]))
        if code:
            for ref in refs:
                conn.execute(
                    "DELETE FROM links WHERE kind IN ('worktree', 'rama') "
                    "AND ref = ? AND ticket_id = ("
                    "  SELECT id FROM tickets WHERE code = ?)",
                    (ref, code),
                )
            conn.commit()
    if args.json:
        print(render.to_json(removed))
    else:
        print(f"Worktree retirado: {target}")
        if removed.get("branch_deleted"):
            print(f"Rama eliminada: {removed['branch_deleted']}")
    return 0


def cmd_worktree_merge(args: argparse.Namespace) -> int:
    root = _root()
    target = args.path
    if target is None:
        if not args.code:
            raise SystemExit("Indica el ticket o --path")
        found = worktree.locate(root, args.code)
        if found is None:
            raise SystemExit(f"No se encontró worktree para {args.code}")
        target = found["path"]
    test_cmd = None
    if not args.no_tests and (Path(target) / "tests" / "run.py").is_file():
        test_cmd = [sys.executable, "tests/run.py"]
    try:
        result = worktree.merge(
            root, target, test_cmd=test_cmd, delete=args.delete
        )
    except TicketError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    if result.get("removed") and args.code:
        with _conn() as conn:
            conn.execute(
                "DELETE FROM links WHERE kind IN ('worktree', 'rama') "
                "AND ticket_id = (SELECT id FROM tickets WHERE code = ?)",
                (args.code,),
            )
            conn.commit()
    if args.json:
        print(render.to_json(result))
        return 0
    print(f"Merge ff-only: {result['branch']} → {result['main_branch']}")
    if result.get("tests"):
        print("Tests: OK")
    if result.get("removed"):
        print(f"Worktree retirado: {result['path']}")
    return 0


def cmd_worktree_adopt(args: argparse.Namespace) -> int:
    root = _root()
    result = worktree.adopt(root, args.path)
    with _conn() as conn:
        ticket = _open_ticket(conn, args.code)
        existing = [
            link["ref"]
            for link in ticket.get("links", [])
            if link["kind"] == "worktree"
        ]
        if existing and not args.force:
            print(
                f"{ticket['code']} ya tiene worktree: {existing[0]}",
                file=sys.stderr,
            )
            return 1
        repo.link_ticket(conn, ticket["code"], "worktree", result["path"])
        if result.get("branch"):
            repo.link_ticket(conn, ticket["code"], "rama", result["branch"])
    if args.json:
        print(render.to_json(result))
    else:
        print(f"Worktree adoptado por {ticket['code']}: {result['path']}")
        if result.get("branch"):
            print(f"Rama: {result['branch']}")
    return 0
