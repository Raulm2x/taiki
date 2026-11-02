"""Comandos ``taiki worktree``: aislamiento por tarea con DB compartida."""

from __future__ import annotations

import argparse
import os
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


def _merge_code(conn, code: str | None, path: str | None) -> str | None:
    """Código del ticket a mergear: por argumento o por el enlace del worktree."""
    if code:
        return code
    if not path:
        return None
    resolved = str(Path(path).expanduser().resolve())
    row = conn.execute(
        "SELECT t.code FROM links l JOIN tickets t ON t.id = l.ticket_id "
        "WHERE l.kind = 'worktree' AND l.ref = ? "
        "ORDER BY l.id DESC LIMIT 1",
        (resolved,),
    ).fetchone()
    return row["code"] if row else None


def _require_confirmed(conn, code: str, force: bool) -> None:
    """Solo se integra en main lo que el usuario ya confirmó."""
    ticket = repo.get_ticket(conn, code)
    if force or ticket["status"] == "confirmado":
        return
    raise TicketError(
        f"{ticket['code']} está '{ticket['status']}'; confírmalo con "
        f"`taiki confirm {ticket['code']}` antes de mergear. "
        "Usa --force solo si es consciente."
    )


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
    print(f"Rama: {result['branch']} (desde {result.get('start') or 'HEAD'})")
    if result.get("warning"):
        print(f"aviso: {result['warning']}", file=sys.stderr)
    if result["bootstrap"]:
        print(f"Config copiada: {', '.join(result['bootstrap'])}")
    print("La base de datos sigue siendo la del checkout principal.")
    print(f"  cd {result['path']} && opencode")
    return 0


def cmd_worktree_list(args: argparse.Namespace) -> int:
    root = _root()
    ref = worktree.integration_ref(root)
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
                "merged": worktree.merged(
                    root, item.get("branch") or "", integration=ref["branch"]
                ),
            }
        )
    if args.json:
        print(
            render.to_json(
                {
                    "integration_branch": ref["branch"],
                    "fallback": ref["fallback"],
                    "warning": ref["warning"],
                    "worktrees": items,
                }
            )
        )
        return 0
    if not items:
        print("No hay worktrees de tareas.")
        return 0
    print(
        f"Rama de integración: {ref['branch']}"
        + (f" (aviso: {ref['warning']})" if ref["warning"] else "")
    )
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
    code = getattr(args, "code", None)
    try:
        with _conn() as conn:
            code = _merge_code(conn, code, getattr(args, "path", None))
            if code:
                _require_confirmed(
                    conn, code, bool(getattr(args, "force", False))
                )
    except TicketError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    if getattr(args, "path", None) and not code:
        print(
            "aviso: no se pudo asociar el worktree a un ticket; "
            "se mergea sin comprobar la confirmación",
            file=sys.stderr,
        )
    target = args.path
    if target is None:
        if not code:
            raise SystemExit("Indica el ticket o --path")
        found = worktree.locate(root, code)
        if found is None:
            raise SystemExit(f"No se encontró worktree para {code}")
        target = found["path"]
    test_cmd = None
    test_env = None
    if not args.no_tests and (Path(target) / "tests" / "run.py").is_file():
        # El presupuesto de la suite es global: el merge lo comprueba con el
        # mismo gate que la CI para que la acumulación de checks falle aquí.
        # Es un gate de integración: se ejecuta con el perfil de CI (CI=true),
        # que da margen de reloj a los runners y al propio merge.
        test_cmd = [sys.executable, "tests/run.py", "--strict-budget"]
        test_env = {**os.environ, "CI": "true"}
    ref = worktree.integration_ref(root)
    if ref["warning"]:
        print(f"aviso: {ref['warning']}", file=sys.stderr)
    try:
        result = worktree.merge(
            root,
            target,
            test_cmd=test_cmd,
            test_env=test_env,
            delete=args.delete,
        )
    except TicketError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    if result.get("removed") and code:
        with _conn() as conn:
            conn.execute(
                "DELETE FROM links WHERE kind IN ('worktree', 'rama') "
                "AND ticket_id = (SELECT id FROM tickets WHERE code = ?)",
                (code,),
            )
            conn.commit()
    failed_removal = bool(result.get("remove_error"))
    if args.json:
        print(render.to_json(result))
        return 1 if failed_removal else 0
    print(
        f"Merge ff-only: {result['branch']} → "
        f"{result.get('integration_branch') or result['main_branch']}"
    )
    if result.get("tests"):
        print("Tests: OK")
    if result.get("removed"):
        print(f"Worktree retirado: {result['path']}")
    if failed_removal:
        print(
            f"Error: el merge se aplicó pero el worktree sigue en {result['path']}",
            file=sys.stderr,
        )
        print(f"Limpieza manual: {result['cleanup']}", file=sys.stderr)
        return 1
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
