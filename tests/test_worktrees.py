"""Worktrees por tarea y resolución de workspace compartido."""

from __future__ import annotations

import argparse
import contextlib
import io
import json
import os
import shutil
import subprocess
import sys
import threading
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


def _merge_reads_stdin(
    root: Path, path: Path, seconds: float = 5.0
) -> tuple[bool, dict]:
    """``merge`` con un ``test_cmd`` que lee stdin no se cuelga.

    El stdin del test se sustituye por una tubería sin datos: sin ``DEVNULL``
    el comando del gate se queda leyendo y el hilo no termina. Al cerrar la
    tubería llega EOF, así que la suite nunca se cuelga esperando al hijo.
    """
    gate = root.parent / "gate_stdin.py"
    gate.write_text(
        "import sys\nsys.stdin.read()\nprint('gate-ok')\n", encoding="utf-8"
    )
    read_fd, write_fd = os.pipe()
    saved_fd = os.dup(0)
    os.dup2(read_fd, 0)
    os.close(read_fd)
    box: dict[str, object] = {}

    def target() -> None:
        try:
            box["merged"] = worktree.merge(
                root, path, delete=True, test_cmd=[sys.executable, str(gate)]
            )
        except BaseException as exc:  # se reporta como fallo del test
            box["error"] = exc

    thread = threading.Thread(target=target, daemon=True)
    try:
        thread.start()
        thread.join(seconds)
        finished = not thread.is_alive()
    finally:
        os.close(write_fd)
        os.dup2(saved_fd, 0)
        os.close(saved_fd)
    thread.join(seconds)
    merged = box.get("merged")
    return finished and "error" not in box, merged if isinstance(merged, dict) else {}


def run(h: Harness) -> None:
    tmp = Path(h.tmp)
    root = git_repo(tmp / "proyecto")
    (root / "src").mkdir()
    (root / "src" / "app.py").write_text("print('hola')\n", encoding="utf-8")
    (root / "opencode.json").write_text("{}\n", encoding="utf-8")
    _commit(root)

    info = workspace.init_workspace(root, name="Proyecto")
    folder = info["workspace"]

    current = _git(root, "branch", "--show-current")
    fallback = worktree.integration_ref(root)
    h.ok(
        fallback["fallback"]
        and fallback["branch"] == current
        and "develop" in (fallback["warning"] or ""),
        "sin develop la integración cae a la rama actual con aviso",
    )

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
    h.ok(
        path.is_dir() and str(path).startswith(str(root / ".worktrees")),
        "worktree.create crea el directorio en .worktrees/ del checkout principal",
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
        (path / "opencode.json").is_file() and not (path / ".taiki").exists(),
        "create copia OpenCode pero no .taiki ni la base de datos",
    )
    h.raises(
        TicketError,
        lambda: worktree.create(root, ticket["code"], title=ticket["title"]),
        "no se crea un segundo worktree para la misma tarea",
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
            (workspace.find_workspace(path), workspace.resolve_db(path)),
            (folder, info["db"]),
            "find_workspace y resolve_db comparten .taiki y DB desde el worktree",
        )
        h.eq(
            (workspace.project_root(path), workspace.resolve(path)["project"]),
            (root, "proyecto"),
            "project_root y resolve usan el checkout principal",
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
    found = worktree.locate(root, ticket["code"])
    clean = worktree.status(path)
    h.ok(
        any(Path(item["path"]) == path for item in listed)
        and found is not None
        and Path(found["path"]) == path
        and clean["exists"]
        and not clean["dirty"],
        "list_worktrees, locate y status ven el worktree limpio",
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
    remaining = _git(root, "branch", "--format=%(refname:short)").split()
    h.ok(
        not tree_path.exists() and branch not in remaining,
        "confirm retira el worktree mergeado y su rama",
    )

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

    # El merge corre la suite del worktree con --strict-budget: una suite que
    # rompe el techo (simulada aquí, para no pagar la suite real) falla y no
    # mergea nada.
    (merge_path / "tests").mkdir()
    (merge_path / "tests" / "run.py").write_text(
        "import sys\n"
        "sys.exit(0 if '--strict-budget' not in sys.argv else 1)\n",
        encoding="utf-8",
    )
    _git(merge_path, "add", "tests/run.py")
    _git(merge_path, "commit", "-m", "suite sintética")
    budget_err = io.StringIO()
    with h.env(TAIKI_DB=str(info["db"])), contextlib.chdir(root), \
            contextlib.redirect_stdout(io.StringIO()), \
            contextlib.redirect_stderr(budget_err):
        budget_rc = cmd_worktree_merge(
            argparse.Namespace(
                code=merge_ticket["code"],
                path=None,
                delete=False,
                no_tests=False,
                json=False,
                force=True,
            )
        )
    h.ok(
        budget_rc == 1
        and "Los tests fallaron" in budget_err.getvalue()
        and not (root / "merge.txt").exists(),
        "el merge ejecuta la suite con --strict-budget y falla si rompe el techo",
    )

    (root / "src" / "app.py").write_text("dirty\n", encoding="utf-8")
    h.raises(
        TicketError,
        lambda: worktree.merge(root, merge_path),
        "merge exige el checkout principal limpio",
    )
    _git(root, "checkout", "--", "src/app.py")
    gate_ok, merged = _merge_reads_stdin(root, merge_path)
    registered = [
        Path(item["path"]).resolve() for item in worktree.list_worktrees(root)
    ]
    h.ok(
        (root / "merge.txt").is_file()
        and not merge_path.exists()
        and merge_path.resolve() not in registered
        and merged.get("removed") is True,
        "merge ff-only integra el cambio y retira el worktree (dir y registro)",
    )
    h.ok(
        merged.get("branch")
        not in _git(root, "branch", "--format=%(refname:short)").split(),
        "merge --delete elimina la rama",
    )
    h.ok(
        gate_ok,
        "merge cierra el stdin del gate: un test_cmd que lee stdin no cuelga",
    )

    # ── merge --delete: si la retirada falla, no se miente ───────────────
    with h.env(TAIKI_DB=str(info["db"])):
        setup = db.connect()
        try:
            stuck_ticket = repo.create_ticket(
                conn=setup, title="Worktree que no se retira", project="proyecto"
            )
            created_stuck = worktree.create(
                root, stuck_ticket["code"], title=stuck_ticket["title"]
            )
            repo.claim_ticket(
                setup, stuck_ticket["code"], agent="opencode"
            )
            repo.complete_ticket(
                setup,
                stuck_ticket["code"],
                resolution="listo",
                actor="opencode",
                actor_type="agente",
            )
            repo.transition(
                setup,
                stuck_ticket["code"],
                "confirmado",
                actor="usuario",
                actor_type="usuario",
            )
        finally:
            setup.close()
    stuck_path = Path(created_stuck["path"])
    (stuck_path / "stuck.txt").write_text("x\n", encoding="utf-8")
    _git(stuck_path, "add", "stuck.txt")
    _git(stuck_path, "commit", "-m", f"stuck {stuck_ticket['code']}")
    _git(root, "worktree", "lock", str(stuck_path))
    stuck_out = io.StringIO()
    stuck_err = io.StringIO()
    with h.env(TAIKI_DB=str(info["db"])), contextlib.chdir(root), \
            contextlib.redirect_stdout(stuck_out), \
            contextlib.redirect_stderr(stuck_err):
        stuck_rc = cmd_worktree_merge(
            argparse.Namespace(
                code=stuck_ticket["code"],
                path=None,
                delete=True,
                no_tests=True,
                json=False,
                force=False,
            )
        )
    h.eq(stuck_rc, 1, "merge --delete falla si el worktree no se retira")
    h.ok(
        "Worktree retirado" not in stuck_out.getvalue(),
        "merge --delete no imprime 'Worktree retirado' si la ruta sigue",
    )
    h.ok(
        "git worktree remove --force" in stuck_err.getvalue(),
        "merge --delete avisa del comando de limpieza manual",
    )
    _git(root, "worktree", "unlock", str(stuck_path))
    worktree.remove(root, stuck_path, force=True, delete_branch=True)

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

    # ── rama de integración: develop ────────────────────────────────────
    _git(root, "checkout", "-b", "develop")
    (root / "develop.txt").write_text("integracion\n", encoding="utf-8")
    _git(root, "add", "develop.txt")
    _git(root, "commit", "-m", "base develop")
    _git(root, "checkout", current)
    integration_tree = worktree.create(root, "T-9001", title="Integración")
    integration_path = Path(integration_tree["path"])
    h.ok(
        (integration_path / "develop.txt").is_file(),
        "la rama agent/… nace de la rama de integración",
    )
    (integration_path / "feature.txt").write_text("x\n", encoding="utf-8")
    _git(integration_path, "add", "-A")
    _git(integration_path, "commit", "-m", "feature")
    merged_into = worktree.merge(root, integration_path)
    h.ok(
        merged_into["integration_branch"] == "develop"
        and _git(root, "show", "develop:feature.txt").strip() == "x"
        and not (root / "feature.txt").exists()
        and _git(root, "branch", "--show-current") == current,
        "merge integra en develop y restaura la rama actual",
    )
    h.ok(
        worktree.merged(root, merged_into["branch"])
        and not worktree.merged(
            root, merged_into["branch"], integration=current
        ),
        "merged compara contra la rama de integración",
    )
    worktree.remove(root, integration_path, force=True, delete_branch=True)
    _git(root, "branch", "-D", "develop")
