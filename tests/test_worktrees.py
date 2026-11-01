"""Worktrees por tarea y resolución de workspace compartido."""

from __future__ import annotations

import subprocess
from pathlib import Path

from ticketlib import db, repo, workspace, worktree
from tests.seeds import git_repo
from tests.support import Harness


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
    h.ok(
        (path / "opencode.json").is_file(),
        "create copia la configuración de OpenCode",
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
