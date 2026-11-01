#!/usr/bin/env python3
"""Smoke opt-in del orquestador real (no se ejecuta en CI).

Uso::

    python3 tests/live_orchestrator.py [--keep]

Crea un repositorio temporal con un workspace Taiki, una épica y dos tareas,
reclama cada tarea con worktree, commitea en su rama y verifica el aislamiento
con ``orchestrator.isolation_problems`` y ``status.snapshot``.

``session_move`` pertenece a OpenCode y no se puede invocar desde este script:
queda como paso manual del checklist. El smoke comprueba el contrato que usa
el prompt (worktree devuelto, rama ``agent/T-XXXX-*``, commit y health MCP).

Este archivo no empieza por ``test_`` para que la suite no lo descubra.
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from ticketlib import db, orchestrator, repo, status, workspace, worktree
from tests.seeds import git_repo


def _git(cwd: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(cwd), *args], capture_output=True, text=True
    )
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or f"git {args} falló")
    return result.stdout.strip()


def run_smoke(keep: bool = False) -> int:
    if subprocess.run(
        ["git", "--version"], capture_output=True
    ).returncode != 0:
        print("BLOQUEO: git no está disponible; no se puede crear el worktree")
        return 2

    tmp = Path(tempfile.mkdtemp(prefix="taiki-live-"))
    root = git_repo(tmp / "repo")
    (root / "README.md").write_text("live\n", encoding="utf-8")
    _git(root, "add", "README.md")
    _git(root, "commit", "-qm", "inicio")

    info = workspace.init_workspace(root, name="Live")
    os.environ["TAIKI_DB"] = str(info["db"])
    os.environ["TAIKI_SHARED_TREE"] = "1"
    os.chdir(root)
    conn = db.connect()
    failures: list[str] = []
    try:
        epic = repo.create_ticket(conn, title="Épica live", project="live")
        tasks = [
            repo.create_ticket(
                conn,
                title=f"Tarea live {index}",
                project="live",
                parent_id=epic["code"],
                paths=[f"src/live{index}.py"],
            )
            for index in (1, 2)
        ]
        for index, task in enumerate(tasks, start=1):
            session = f"ses_live_{index}"
            repo.claim_ticket(
                conn, task["code"], agent="worker", session_id=session
            )
            created = worktree.create(root, task["code"], title=task["title"])
            wt = Path(created["path"])
            target = wt / f"src/live{index}.py"
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(f"# live {index}\n", encoding="utf-8")
            _git(wt, "add", f"src/live{index}.py")
            _git(wt, "commit", "-qm", f"live {index} ({task['code']})")
            branch = _git(wt, "rev-parse", "--abbrev-ref", "HEAD")
            commit = _git(wt, "rev-parse", "HEAD")
            problems = orchestrator.isolation_problems(
                task["code"],
                worktree=str(wt),
                directory=str(wt),
                branch=branch,
                commit=commit,
            )
            if problems:
                failures.extend(
                    f"{task['code']}: {problem}" for problem in problems
                )
            repo.complete_ticket(
                conn,
                task["code"],
                resolution=f"live {index}",
                commit=commit,
                actor="worker",
                session_id=session,
            )
            repo.transition(
                conn,
                task["code"],
                "verificado",
                note="gates live verdes",
                actor="conductor",
            )
            print(
                f"{task['code']}: branch={branch} commit={commit[:8]} "
                f"worktree={wt}"
            )
        data = status.snapshot(with_servers=False, conn=conn)
        print(
            "health MCP: "
            f"queue={data['queue']} sessions={len(data['sessions'])}"
        )
        if data["queue"]["verifying"] != 2:
            failures.append(
                f"esperaba 2 verificados, hay {data['queue']['verifying']}"
            )
        print("session_move: paso manual de OpenCode (no invocable aquí)")
        print("checklist: worktree OK · rama agent/T-XXXX-* OK · commit OK · health OK")
    finally:
        conn.close()
    if failures:
        print("\nFALLOS:")
        for item in failures:
            print(f"  - {item}")
        return 1
    print(f"\nOK: smoke live en {tmp}")
    if not keep:
        shutil.rmtree(tmp, ignore_errors=True)
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Smoke opt-in del orquestador")
    parser.add_argument(
        "--keep",
        action="store_true",
        help="No borrar el directorio temporal (por defecto se conserva para evidencia)",
    )
    args = parser.parse_args(argv)
    return run_smoke(keep=args.keep)


if __name__ == "__main__":
    raise SystemExit(main())
