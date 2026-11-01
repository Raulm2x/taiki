"""Worktrees por tarea y resolución de workspace compartido."""

from __future__ import annotations

import argparse
import contextlib
import io
import json
import shutil
import subprocess
from pathlib import Path

from commands.tickets import cmd_claim
from commands.workflow import cmd_confirm
from commands.worktree import cmd_worktree_merge
from ticketlib import db, render, repo, workspace, worktree
from ticketlib.domain import TicketError
from tests.seeds import git_repo
from tests.support import Harness


def _git(root: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(root), *args], capture_output=True, text=True
    )
    if result.returncode != 0:
        raise AssertionError(result.stderr)
    return result.stdout.strip()


def _commit(repo_dir: Path, message: str = "init") -> None:
    subprocess.run(["git", "-C", str(repo_dir), "add", "-A"], check=True)
    subprocess.run(
        ["git", "-C", str(repo_dir), "commit", "-m", message],
        check=True,
        capture_output=True,
    )


def run(h: Harness) -> None:
    tmp = Path(h.tmp)
    root = git_repo(tmp / "proyecto")
    (root / "src").mkdir()
    (root / "src" / "app.py").write_text("print('hola')\n", encoding="utf-8")
    (root / "opencode.json").write_text("{}\n", encoding="utf-8")
    _commit(root)

    info = workspace.init_workspace(root, name="Proyecto")
    folder = info["workspace"]

    # ── Worktree creado por Taiki ───────────────────────────────────────
    with h.env(TAIKI_DB=str(info["db"])):
        conn = db.connect()
        try:
            ticket = repo.create_ticket(
                conn, title="Aislar escritura", project="proyecto"
            )
            created = worktree.create(
                root, ticket["code"], title=ticket["title"]
            )
        finally:
            conn.close()

    path = created["path"]
    h.ok(path.is_dir(), "worktree.create crea el directorio")
    h.ok(
        str(path).startswith(str(root / ".worktrees")),
        "el worktree vive en .worktrees/ del checkout principal",
    )
    h.ok(
        created["branch"].startswith(f"agent/{ticket['code']}"),
        "la rama usa agent/<T-XXXX>-<slug>",
    )
    serialized = json.loads(render.to_json(created))
    h.ok(
        serialized["path"] and serialized["main"],
        "worktree create se serializa a JSON (Path incluido)",
    )
    h.ok(
        (path / "opencode.json").is_file(),
        "create copia la configuración de OpenCode",
    )
    h.raises(
        TicketError,
        lambda: worktree.create(root, ticket["code"], title=ticket["title"]),
        "no se crea un segundo worktree para la misma tarea",
    )
    h.ok(
        not (path / ".taiki").exists(),
        "el worktree no copia .taiki ni la base de datos",
    )

    ignored = subprocess.run(
        ["git", "-C", str(root), "check-ignore", ".worktrees/x"],
        capture_output=True,
        text=True,
    )
    h.eq(ignored.returncode, 0, ".worktrees/ queda ignorado por Git")

    # ── Resolución de workspace desde el worktree ───────────────────────
    with h.env(TAIKI_DB=None, TICKETS_DB=None):
        h.eq(
            workspace.find_workspace(path),
            folder,
            "find_workspace encuentra el .taiki principal desde el worktree",
        )
        h.eq(
            workspace.resolve_db(path),
            info["db"],
            "el worktree usa la base de datos compartida",
        )
        h.eq(
            workspace.project_root(path),
            root,
            "project_root no hereda el nombre T-XXXX del worktree",
        )
        h.eq(
            workspace.resolve(path)["project"],
            "proyecto",
            "resolve devuelve el proyecto del checkout principal",
        )
        # En un repo real, `.taiki/config.json` viaja al worktree por Git.
        # No debe convertir el worktree en un workspace con DB vacía.
        (path / ".taiki").mkdir()
        (path / ".taiki" / "config.json").write_text("{}\n", encoding="utf-8")
        try:
            h.eq(
                workspace.find_workspace(path),
                folder,
                "un .taiki versionado en el worktree no crea una DB local",
            )
            h.eq(
                workspace.resolve_db(path),
                info["db"],
                "el .taiki del worktree sigue apuntando a la DB principal",
            )
        finally:
            shutil.rmtree(path / ".taiki")

    # ── Listado, estado y avisos ────────────────────────────────────────
    listed = worktree.list_worktrees(root)
    h.ok(
        any(Path(item["path"]) == path for item in listed),
        "list_worktrees incluye el worktree creado",
    )
    found = worktree.locate(root, ticket["code"])
    h.ok(
        found is not None and Path(found["path"]) == path,
        "locate encuentra el worktree por ticket",
    )
    clean = worktree.status(path)
    h.ok(
        clean["exists"] and not clean["dirty"],
        "status informa de un worktree limpio",
    )
    (path / "src" / "app.py").write_text("print('cambio')\n", encoding="utf-8")
    dirty = worktree.status(path)
    h.ok(dirty["dirty"], "status detecta cambios sin commitear")
    notices = worktree.warnings(
        root, [{"code": ticket["code"], "path": str(path), "branch": created["branch"]}]
    )
    h.ok(
        any("sin commitear" in item for item in notices),
        "warnings avisa del worktree sucio",
    )

    # ── Worktree externo (fuera del repo) y adopción ────────────────────
    external = tmp / "externo-T-9999"
    subprocess.run(
        [
            "git",
            "-C",
            str(root),
            "worktree",
            "add",
            "-b",
            "agent/externo",
            str(external),
        ],
        check=True,
        capture_output=True,
    )
    with h.env(TAIKI_DB=None, TICKETS_DB=None):
        h.eq(
            workspace.resolve_db(external),
            info["db"],
            "un worktree fuera del repo también resuelve a la DB principal",
        )
    adopted = worktree.adopt(root, external)
    h.eq(str(external), adopted["path"], "adopt valida un worktree existente")
    h.raises(
        Exception,
        lambda: worktree.adopt(root, tmp / "no-existe"),
        "adopt rechaza una ruta que no es worktree",
    )

    # ── Dos worktrees no comparten árbol ni índice ──────────────────────
    twin = tmp / "gemelo"
    subprocess.run(
        ["git", "-C", str(root), "worktree", "add", "-b", "agent/gemelo", str(twin)],
        check=True,
        capture_output=True,
    )
    (path / "solo-a.txt").write_text("a\n", encoding="utf-8")
    (twin / "solo-b.txt").write_text("b\n", encoding="utf-8")
    subprocess.run(
        ["git", "-C", str(twin), "add", "solo-b.txt"], check=True
    )
    staged_a = subprocess.run(
        ["git", "-C", str(path), "diff", "--cached", "--name-only"],
        capture_output=True,
        text=True,
    ).stdout.strip()
    h.ok(
        not (twin / "solo-a.txt").exists(),
        "los worktrees no comparten árbol de trabajo",
    )
    h.eq(staged_a, "", "cada worktree tiene su propio índice")
    subprocess.run(
        ["git", "-C", str(root), "worktree", "remove", "--force", str(twin)],
        check=True,
        capture_output=True,
    )

    # ── Retirada ────────────────────────────────────────────────────────
    removed = worktree.remove(root, path, force=True, delete_branch=True)
    h.ok(
        not path.exists() and removed["branch_deleted"] == created["branch"],
        "remove retira el worktree y su rama",
    )
    subprocess.run(
        ["git", "-C", str(root), "worktree", "remove", "--force", str(external)],
        check=True,
        capture_output=True,
    )

    # ── claim --worktree y limpieza al confirmar ────────────────────────
    with h.env(TAIKI_DB=str(info["db"])):
        setup = db.connect()
        try:
            with_tree = repo.create_ticket(
                conn=setup, title="Con worktree", project="proyecto"
            )
            dirty_ticket = repo.create_ticket(
                conn=setup, title="Worktree sucio", project="proyecto"
            )
        finally:
            setup.close()

    with h.env(TAIKI_DB=str(info["db"])), contextlib.chdir(root), \
            contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
        h.eq(
            cmd_claim(
                argparse.Namespace(
                    code=with_tree["code"],
                    agent="opencode",
                    force=False,
                    steal=False,
                    worktree=True,
                )
            ),
            0,
            "taiki claim --worktree responde",
        )
    tree_path = root / ".worktrees" / with_tree["code"]
    h.ok(tree_path.is_dir(), "claim --worktree crea el worktree")

    (tree_path / "nuevo.txt").write_text("x\n", encoding="utf-8")
    _git(tree_path, "add", "-A")
    _git(tree_path, "commit", "-m", f"cambio {with_tree['code']}")
    with h.env(TAIKI_DB=str(info["db"])):
        reader = db.connect()
        try:
            links = repo.get_ticket(reader, with_tree["code"])["links"]
            branch = next(item["ref"] for item in links if item["kind"] == "rama")
            repo.complete_ticket(
                reader, with_tree["code"], resolution="mergeado"
            )
        finally:
            reader.close()
    _git(root, "merge", "--ff-only", branch)
    out = io.StringIO()
    with h.env(TAIKI_DB=str(info["db"])), contextlib.chdir(root), \
            contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()):
        h.eq(
            cmd_confirm(
                argparse.Namespace(
                    code=[with_tree["code"]],
                    note=None,
                    keep_worktrees=False,
                )
            ),
            0,
            "confirm responde tras limpiar el worktree",
        )
    h.ok(not tree_path.exists(), "confirm retira el worktree mergeado")
    remaining = _git(root, "branch", "--format=%(refname:short)").split()
    h.ok(branch not in remaining, "confirm elimina la rama del worktree")

    # Worktree sucio: se conserva y se avisa
    with h.env(TAIKI_DB=str(info["db"])), contextlib.chdir(root), \
            contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
        cmd_claim(
            argparse.Namespace(
                code=dirty_ticket["code"],
                agent="opencode",
                force=False,
                steal=False,
                worktree=True,
            )
        )
    dirty_path = root / ".worktrees" / dirty_ticket["code"]
    (dirty_path / "pendiente.txt").write_text("x\n", encoding="utf-8")
    with h.env(TAIKI_DB=str(info["db"])):
        reader = db.connect()
        try:
            repo.complete_ticket(
                reader, dirty_ticket["code"], resolution="sin commitear"
            )
        finally:
            reader.close()
    errors = io.StringIO()
    with h.env(TAIKI_DB=str(info["db"])), contextlib.chdir(root), \
            contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(errors):
        cmd_confirm(
            argparse.Namespace(
                code=[dirty_ticket["code"]],
                note=None,
                keep_worktrees=False,
            )
        )
    h.ok(dirty_path.exists(), "un worktree sucio se conserva al confirmar")
    h.ok("sin commitear" in errors.getvalue(), "confirm avisa del worktree sucio")
    worktree.remove(root, dirty_path, force=True, delete_branch=True)

    # ── merge: rebase + ff-only + limpieza ──────────────────────────────
    with h.env(TAIKI_DB=str(info["db"])):
        setup = db.connect()
        try:
            merge_ticket = repo.create_ticket(
                conn=setup, title="Para mergear", project="proyecto"
            )
        finally:
            setup.close()
    with h.env(TAIKI_DB=str(info["db"])), contextlib.chdir(root), \
            contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
        cmd_claim(
            argparse.Namespace(
                code=merge_ticket["code"],
                agent="opencode",
                force=False,
                steal=False,
                worktree=True,
            )
        )
    merge_path = root / ".worktrees" / merge_ticket["code"]
    (merge_path / "merge.txt").write_text("x\n", encoding="utf-8")
    _git(merge_path, "add", "-A")
    _git(merge_path, "commit", "-m", f"merge {merge_ticket['code']}")
    (root / "src" / "app.py").write_text("dirty\n", encoding="utf-8")
    h.raises(
        TicketError,
        lambda: worktree.merge(root, merge_path),
        "merge exige el checkout principal limpio",
    )
    _git(root, "checkout", "--", "src/app.py")
    merged = worktree.merge(root, merge_path, delete=True)
    h.ok(
        (root / "merge.txt").is_file() and not merge_path.exists(),
        "merge ff-only integra el cambio y retira el worktree",
    )
    h.ok(
        merged["branch"] not in _git(root, "branch", "--format=%(refname:short)").split(),
        "merge --delete elimina la rama",
    )

    # ── merge exige ticket confirmado ──────────────────────────────────
    with h.env(TAIKI_DB=str(info["db"])), contextlib.chdir(root):
        conn = db.connect()
        try:
            gate = repo.create_ticket(conn, title="Guard de merge")
            repo.claim_ticket(conn, gate["code"], agent="opencode")
            repo.complete_ticket(
                conn,
                gate["code"],
                resolution="listo",
                actor="opencode",
                actor_type="agente",
            )
            args = argparse.Namespace(
                code=gate["code"],
                path=None,
                delete=False,
                no_tests=True,
                json=False,
                force=False,
            )
            stderr = io.StringIO()
            with contextlib.redirect_stdout(io.StringIO()), \
                    contextlib.redirect_stderr(stderr):
                rc = cmd_worktree_merge(args)
            h.eq(rc, 1, "merge rechaza un ticket completado")
            h.ok(
                "taiki confirm" in stderr.getvalue().lower(),
                "el rechazo pide confirmar el ticket antes de mergear",
            )

            repo.transition(
                conn,
                gate["code"],
                "confirmado",
                actor="usuario",
                actor_type="usuario",
            )
            h.raises(
                SystemExit,
                lambda: cmd_worktree_merge(args),
                "un ticket confirmado sin worktree llega a la búsqueda",
            )

            forced = repo.create_ticket(conn, title="Guard con force")
            repo.claim_ticket(conn, forced["code"], agent="opencode")
            repo.complete_ticket(
                conn,
                forced["code"],
                resolution="listo",
                actor="opencode",
                actor_type="agente",
            )
            args.code = forced["code"]
            args.force = True
            h.raises(
                SystemExit,
                lambda: cmd_worktree_merge(args),
                "--force salta el estado y llega a la búsqueda del worktree",
            )
        finally:
            conn.close()
