"""Commit seguro por rutas y detección de solapamientos."""

from __future__ import annotations

import argparse
import contextlib
import io
import subprocess
from pathlib import Path

from commands.commit import cmd_commit, cmd_hook_check_index
from ticketlib import audit as audit_lib, commits, db, repo, workspace
from tests.seeds import git_repo
from tests.support import Harness


def _git(root: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(root), *args], capture_output=True, text=True
    )
    if result.returncode != 0:
        raise AssertionError(result.stderr)
    return result.stdout


def run(h: Harness) -> None:
    tmp = Path(h.tmp)
    root = git_repo(tmp / "repo")
    (root / "src").mkdir()
    (root / "src" / "a.py").write_text("a\n", encoding="utf-8")
    (root / "docs").mkdir()
    (root / "docs" / "b.md").write_text("b\n", encoding="utf-8")
    _git(root, "add", "-A")
    _git(root, "commit", "-m", "inicio")

    info = workspace.init_workspace(root, name="Repo")
    with h.env(TAIKI_DB=str(info["db"])):
        conn = db.connect()
        try:
            ticket = repo.create_ticket(
                conn,
                title="Cambio en src",
                project="repo",
                paths=["src/"],
            )
            other = repo.create_ticket(
                conn,
                title="Cambio en docs",
                project="repo",
                paths=["docs/"],
            )
            repo.claim_ticket(
                conn, ticket["code"], agent="opencode", session_id="ses_a"
            )
            repo.claim_ticket(
                conn, other["code"], agent="opencode", session_id="ses_b"
            )
            h.eq(ticket["paths"], ["src/"], "create guarda las rutas del ticket")
            updated = repo.update_ticket(
                conn, ticket["code"], paths=["src/", "tests/"]
            )
            h.eq(
                updated["paths"],
                ["src/", "tests/"],
                "update reemplaza las rutas del ticket",
            )
            repo.update_ticket(conn, ticket["code"], paths=["src/"])

            # ── Índice dentro del alcance ───────────────────────────────
            (root / "src" / "a.py").write_text("a2\n", encoding="utf-8")
            _git(root, "add", "src/a.py")
            report = commits.check(
                root,
                repo.get_ticket(conn, ticket["code"]),
                [{"code": other["code"], "paths": ["docs/"]}],
            )
            h.ok(
                report["ok"] and report["staged"] == ["src/a.py"],
                "el staging dentro del alcance pasa la comprobación",
            )
            args = argparse.Namespace(
                message="cambio en src", ticket=ticket["code"], dry_run=False, json=False
            )
            with contextlib.chdir(root), contextlib.redirect_stdout(
                io.StringIO()
            ), contextlib.redirect_stderr(io.StringIO()):
                code = cmd_commit(args)
            h.eq(code, 0, "taiki commit acepta las rutas del ticket")
            subject = _git(root, "log", "-1", "--format=%s").strip()
            h.ok(
                ticket["code"] in subject,
                "el mensaje incluye el código del ticket",
            )
            linked = repo.get_ticket(conn, ticket["code"])
            h.ok(
                any(item["kind"] == "commit" for item in linked["links"]),
                "el commit queda enlazado al ticket",
            )

            # ── Índice fuera del alcance ────────────────────────────────
            (root / "docs" / "b.md").write_text("b2\n", encoding="utf-8")
            _git(root, "add", "docs/b.md")
            report = commits.check(
                root,
                repo.get_ticket(conn, ticket["code"]),
                [{"code": other["code"], "paths": ["docs/"]}],
            )
            h.ok(
                report["outside"] == ["docs/b.md"]
                and report["conflicts"]
                and not report["ok"],
                "detecta rutas fuera de alcance y solapamiento",
            )
            args = argparse.Namespace(
                message="mezcla", ticket=ticket["code"], dry_run=True, json=False
            )
            with contextlib.chdir(root), contextlib.redirect_stdout(
                io.StringIO()
            ), contextlib.redirect_stderr(io.StringIO()):
                code = cmd_commit(args)
            h.eq(code, 1, "taiki commit rechaza el staged ajeno")
            with h.env(TAIKI_TICKET=ticket["code"]), contextlib.chdir(
                root
            ), contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(
                io.StringIO()
            ):
                blocked = cmd_hook_check_index(argparse.Namespace(ticket=None))
            h.eq(blocked, 1, "hook-check-index bloquea rutas ajenas")
            _git(root, "reset", "-q", "docs/b.md")
            _git(root, "checkout", "--", "docs/b.md")

            # ── Solapamientos en audit ──────────────────────────────────
            repo.update_ticket(conn, other["code"], paths=["src/a.py"])
            pairs = audit_lib.overlaps(conn)
            h.ok(
                any(
                    {item["a"], item["b"]} == {ticket["code"], other["code"]}
                    for item in pairs
                ),
                "audit detecta solapamiento entre tickets activos",
            )
            h.ok(
                "docs/b.md" not in audit_lib.dirty(root)
                and "docs/b.md" not in commits.unstaged(root),
                "sin cambios pendientes el checkout está limpio",
            )
            (root / "suelto.txt").write_text("x\n", encoding="utf-8")
            h.ok(
                "suelto.txt" in audit_lib.dirty(root),
                "audit lista los ficheros sin seguimiento",
            )
        finally:
            conn.close()
