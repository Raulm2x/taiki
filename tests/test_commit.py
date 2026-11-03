"""Commit seguro por rutas y detección de solapamientos."""

from __future__ import annotations

import argparse
import contextlib
import io
import subprocess
from pathlib import Path

from commands.commit import cmd_commit, cmd_hook_check_index
from commands.workflow import cmd_reject
from ticketlib import audit as audit_lib, commits, db, repo, workspace
from ticketlib.util import clean_paths
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

    # ── Rutas con punto inicial se conservan ────────────────────────────
    h.eq(
        clean_paths(
            [
                ".github/x",
                ".env",
                ".taiki/config.json",
                "./src/a.py",
                "../fuera",
            ]
        ),
        [".github/x", ".env", ".taiki/config.json", "src/a.py", "../fuera"],
        "clean_paths solo retira el prefijo ./",
    )
    h.ok(
        commits.matches(".github/workflows/ci.yml", [".github/workflows/"])
        and bool(commits.overlaps([".github/workflows/"], [".github/workflows/ci.yml"])),
        "matches y overlaps reconocen rutas ocultas",
    )
    h.eq(
        commits.overlaps([".env"], ["env"]),
        [],
        "no confunde .env con env",
    )

    # ── Rama live: solo none/scope fuera de un worktree ─────────────────
    h.ok(
        commits.branch_policy(
            "main", "T-0001", agent=True, isolation="none", live_branch="main"
        )["ok"]
        and commits.branch_policy(
            "main", "T-0001", agent=True, isolation="scope", live_branch="main"
        )["ok"],
        "la rama live acepta commits de agente con ticket en none/scope",
    )
    h.ok(
        not commits.branch_policy(
            "main",
            "T-0001",
            agent=True,
            isolation="none",
            live_branch="main",
            worktree=True,
        )["ok"]
        and not commits.branch_policy(
            "main", "T-0001", agent=True, isolation="worktree", live_branch="main"
        )["ok"]
        and not commits.branch_policy(
            "develop", "T-0001", agent=True, isolation="none", live_branch="main"
        )["ok"],
        "main sigue bloqueado en worktree/worktree y la excepción live es solo la rama configurada",
    )

    root = git_repo(tmp / "repo")
    (root / "src").mkdir()
    (root / "src" / "a.py").write_text("a\n", encoding="utf-8")
    (root / "docs").mkdir()
    (root / "docs" / "b.md").write_text("b\n", encoding="utf-8")
    _git(root, "add", "-A")
    _git(root, "commit", "-m", "inicio")

    info = workspace.init_workspace(root, name="Repo")
    with h.env(TAIKI_DB=str(info["db"]), TAIKI_TICKET=None):
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
            h.ok(
                ticket["paths"] == ["src/"]
                and repo.update_ticket(
                    conn, ticket["code"], paths=["src/", "tests/"]
                )["paths"]
                == ["src/", "tests/"],
                "create guarda las rutas y update las reemplaza",
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
            # El hook pre-commit exige TAIKI_TICKET: con dos tickets activos,
            # `taiki commit --ticket` debe exportarlo al subproceso de git.
            hook = root / ".git" / "hooks" / "pre-commit"
            hook.write_text(
                '#!/bin/sh\n[ -n "$TAIKI_TICKET" ] || '
                '{ echo "pre-commit: define TAIKI_TICKET" >&2; exit 1; }\n',
                encoding="utf-8",
            )
            hook.chmod(0o755)
            args = argparse.Namespace(
                message="cambio en src",
                ticket=ticket["code"],
                agent="worker",
                session="ses_commit",
                dry_run=False,
                json=False,
            )
            with contextlib.chdir(root), contextlib.redirect_stdout(
                io.StringIO()
            ), contextlib.redirect_stderr(io.StringIO()):
                code = cmd_commit(args)
            h.eq(code, 0, "taiki commit exporta TAIKI_TICKET y el hook lo acepta")
            h.eq(
                _git(root, "log", "-1", "--format=%B").strip(),
                f"cambio en src ({ticket['code']})",
                "un solo -m: asunto con código y sin cuerpo extra",
            )

            # ── Varios -m se concatenan como párrafos (git) ────────────
            (root / "src" / "a.py").write_text("a3\n", encoding="utf-8")
            _git(root, "add", "src/a.py")
            with contextlib.chdir(root), contextlib.redirect_stdout(
                io.StringIO()
            ), contextlib.redirect_stderr(io.StringIO()):
                code = cmd_commit(
                    argparse.Namespace(
                        message=["feat: cambio visible", "Release-Bump: patch"],
                        ticket=ticket["code"],
                        agent="worker",
                        session="ses_commit",
                        dry_run=False,
                        json=False,
                    )
                )
            h.ok(
                code == 0
                and _git(root, "log", "-1", "--format=%B").strip()
                == f"feat: cambio visible ({ticket['code']})\n\nRelease-Bump: patch",
                "varios -m: código en el asunto y párrafos separados por línea en blanco",
            )

            linked = repo.get_ticket(conn, ticket["code"])
            link_event = next(
                event
                for event in linked["events"]
                if event["action"] == "enlazar"
                and (event.get("note") or "").startswith("commit:")
            )
            h.ok(
                any(item["kind"] == "commit" for item in linked["links"])
                and link_event["actor"] == "worker"
                and link_event["actor_type"] == "agente"
                and link_event["session_id"] == "ses_commit",
                "el commit queda enlazado y atribuido al agente",
            )

            # ── Sin --ticket y varios activos: no adivina ──────────────
            stderr = io.StringIO()
            with contextlib.chdir(root), contextlib.redirect_stdout(
                io.StringIO()
            ), contextlib.redirect_stderr(stderr):
                sin_ticket = cmd_commit(
                    argparse.Namespace(
                        message="sin ticket",
                        ticket=None,
                        agent="worker",
                        session="ses_commit",
                        dry_run=False,
                        json=False,
                    )
                )
            h.ok(
                sin_ticket == 1 and "varios tickets activos" in stderr.getvalue(),
                "sin --ticket y varios activos, taiki commit falla con el mensaje esperado",
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
            with contextlib.chdir(root), contextlib.redirect_stdout(
                io.StringIO()
            ), contextlib.redirect_stderr(io.StringIO()):
                ambiguous = cmd_hook_check_index(argparse.Namespace(ticket=None))
            h.eq(
                ambiguous,
                1,
                "hook-check-index bloquea si hay varios tickets activos",
            )
            _git(root, "reset", "-q", "docs/b.md")
            _git(root, "checkout", "--", "docs/b.md")

            # ── Scope estricto: fuera y vacío (B7, fail-closed) ─────────
            scoped = repo.create_ticket(
                conn,
                title="Scope estricto",
                project="repo",
                isolation="scope",
                paths=["src/"],
            )
            (root / "docs" / "b.md").write_text("b3\n", encoding="utf-8")
            _git(root, "add", "docs/b.md")
            scoped_report = commits.check(
                root, repo.get_ticket(conn, scoped["code"])
            )
            _git(root, "reset", "-q", "docs/b.md")
            _git(root, "checkout", "--", "docs/b.md")

            empty_scope = repo.create_ticket(
                conn,
                title="Scope vacío",
                project="repo",
                isolation="scope",
                paths=[],
            )
            (root / "src" / "a.py").write_text("a4\n", encoding="utf-8")
            _git(root, "add", "src/a.py")
            empty_report = commits.check(
                root, repo.get_ticket(conn, empty_scope["code"])
            )
            h.ok(
                scoped_report["outside"] == ["docs/b.md"]
                and scoped_report["isolation"] == "scope"
                and not scoped_report["ok"]
                and empty_report["scope_error"] is not None
                and empty_report["outside"] == ["src/a.py"]
                and not empty_report["ok"],
                "scope rechaza el staged fuera y el scope vacío deniega todo (fail-closed)",
            )
            _git(root, "reset", "-q", "src/a.py")
            _git(root, "checkout", "--", "src/a.py")

            # ── --repo: commit en el árbol vivo y enlace external ───────
            live = git_repo(tmp / "live")
            (live / "dot.txt").write_text("x\n", encoding="utf-8")
            _git(live, "add", "dot.txt")
            live_ticket = repo.create_ticket(
                conn,
                title="Dotfiles live",
                project="live",
                isolation="none",
            )
            repo.claim_ticket(
                conn,
                live_ticket["code"],
                agent="worker",
                session_id="ses_live",
            )
            with h.env(TAIKI_DB=str(info["db"])), contextlib.chdir(
                root
            ), contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(
                io.StringIO()
            ):
                repo_code = cmd_commit(
                    argparse.Namespace(
                        message="dotfiles",
                        ticket=live_ticket["code"],
                        repo=str(live),
                        agent="worker",
                        session="ses_live",
                        dry_run=False,
                        json=False,
                    )
                )
            live_sha = _git(live, "rev-parse", "--short", "HEAD").strip()
            live_links = [
                item
                for item in repo.get_ticket(conn, live_ticket["code"])["links"]
                if item["kind"] == "external"
            ]
            h.ok(
                repo_code == 0
                and live_links
                and live_links[0]["ref"] == f"live@{live_sha}",
                "taiki commit --repo commitea en el repo live y enlaza external repo@sha",
            )

            # ── --repo traduce paths absolutos del ticket al repo live ──
            dots = git_repo(tmp / "dots")
            (dots / "hypr").mkdir()
            (dots / "hypr" / "a.py").write_text("a\n", encoding="utf-8")
            (dots / "other").mkdir()
            (dots / "other" / "b.py").write_text("b\n", encoding="utf-8")
            _git(dots, "add", "-A")
            _git(dots, "commit", "-m", "inicio")
            dots_ticket = repo.create_ticket(
                conn,
                title="Dotfiles con path absoluto",
                project="dots",
                isolation="scope",
                paths=[str(dots / "hypr")],
            )
            repo.claim_ticket(
                conn,
                dots_ticket["code"],
                agent="worker",
                session_id="ses_dots",
            )
            (dots / "hypr" / "a.py").write_text("a2\n", encoding="utf-8")
            _git(dots, "add", "hypr/a.py")
            translated = commits.check(
                dots,
                repo.get_ticket(conn, dots_ticket["code"]),
                repo_root=dots,
            )
            (dots / "other" / "b.py").write_text("b2\n", encoding="utf-8")
            _git(dots, "add", "other/b.py")
            away = commits.check(
                dots,
                repo.get_ticket(conn, dots_ticket["code"]),
                repo_root=dots,
            )
            _git(dots, "reset", "-q", "other/b.py")
            with h.env(TAIKI_DB=str(info["db"])), contextlib.redirect_stdout(
                io.StringIO()
            ), contextlib.redirect_stderr(io.StringIO()):
                dots_code = cmd_commit(
                    argparse.Namespace(
                        message="hypr",
                        ticket=dots_ticket["code"],
                        repo=str(dots),
                        agent="worker",
                        session="ses_dots",
                        dry_run=True,
                        json=False,
                    )
                )
            h.ok(
                translated["ok"]
                and translated["scope"] == [str(dots / "hypr")]
                and translated["commit_scope"] == ["hypr"]
                and away["outside"] == ["other/b.py"]
                and not away["ok"]
                and dots_code == 0,
                "repo_root traduce el path absoluto: dentro pasa, fuera no, "
                "y taiki commit --repo lo acepta",
            )

            # ── Solapamientos en audit ──────────────────────────────────
            repo.update_ticket(conn, other["code"], paths=["src/a.py"])
            pairs = audit_lib.overlaps(conn)
            overlaps = any(
                {item["a"], item["b"]} == {ticket["code"], other["code"]}
                for item in pairs
            )
            clean = (
                "docs/b.md" not in audit_lib.dirty(root)
                and "docs/b.md" not in commits.unstaged(root)
            )
            (root / "suelto.txt").write_text("x\n", encoding="utf-8")
            untracked = "suelto.txt" in audit_lib.dirty(root)
            h.ok(
                overlaps and clean and untracked,
                "audit detecta solapamientos, lista sin seguimiento y "
                "el checkout queda limpio",
            )
        finally:
            conn.close()

    # ── reject --revert: manual por CLI y rework normal intacto ─────────
    live = repo.create_ticket(h.conn, title="CLI revert", isolation="none")
    repo.update_ticket(
        h.conn,
        live["code"],
        reversal={"kind": "copy", "command": "echo hola"},
    )
    with contextlib.redirect_stdout(io.StringIO()) as out:
        manual_code = cmd_reject(
            argparse.Namespace(code=live["code"], why="manual", revert=True)
        )
    normal = repo.create_ticket(h.conn, title="CLI rework", isolation="none")
    with contextlib.redirect_stdout(io.StringIO()):
        normal_code = cmd_reject(
            argparse.Namespace(code=normal["code"], why="rework", revert=False)
        )
    h.ok(
        manual_code == 0
        and "ejecuta: echo hola" in out.getvalue()
        and repo.get_ticket(h.conn, live["code"])["status"] == "cancelado"
        and normal_code == 0
        and repo.get_ticket(h.conn, normal["code"])["status"] == "en_progreso",
        "reject --revert imprime el comando manual y sin flag mantiene el rework",
    )
