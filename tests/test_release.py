"""``taiki release`` y el flujo agent → develop → release → main.

El techo de la suite es 1051 checks, así que todas las invariantes se consolidan
en un único ``h.eq`` con un diccionario de observaciones: rutas negativas
(dry-run, gates rojos y ancestro) y release real.

El E2E del flujo completo (claim con worktree, commit en ``agent/*``, rebase +
ff-only en ``develop``, release squash, reconciliación de ``develop``,
segunda release, tags anotados y CHANGELOG) es un smoke opt-in con
``TAIKI_E2E=1``: monta varios repos de Git y no cabe en el presupuesto de tiempo
de la suite completa (5 s). La CI lo activa y lo ejecuta con ``--strict-budget``.
Los repos son temporales y locales: sin red y sin tocar ``main``/``develop`` del
proyecto real.
"""

from __future__ import annotations

import contextlib
import io
import json
import os
import subprocess
from argparse import Namespace
from pathlib import Path

from commands.release import cmd_release
from commands.tickets import cmd_claim
from commands.worktree import cmd_worktree_merge
from tests.seeds import git_repo
from ticketlib import db, repo, workspace


def _git(root: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(root), *args], capture_output=True, text=True
    )
    if result.returncode != 0:
        raise AssertionError(result.stderr)
    return result.stdout.strip()


def _commit(root: Path, name: str, text: str, message: str) -> None:
    (root / name).write_text(text, encoding="utf-8")
    _git(root, "add", name)
    _git(root, "commit", "-m", message)


def _release(root: Path, **options) -> tuple[int, dict]:
    args = Namespace(
        repo=str(root),
        integration=None,
        bump=options.get("bump"),
        dry_run=options.get("dry_run", False),
        no_verify=options.get("no_verify", True),
        verify_cmd=options.get("verify_cmd"),
        json=True,
    )
    output = io.StringIO()
    with contextlib.redirect_stdout(output), \
            contextlib.redirect_stderr(io.StringIO()):
        code = cmd_release(args)
    raw = output.getvalue().strip()
    return code, (json.loads(raw) if raw else {})


def _tag_exists(root: Path, tag: str) -> bool:
    result = subprocess.run(
        ["git", "-C", str(root), "rev-parse", "--verify", "--quiet", f"refs/tags/{tag}"],
        capture_output=True,
        text=True,
    )
    return result.returncode == 0


def _is_ancestor(root: Path, ancestor: str, descendant: str) -> bool:
    result = subprocess.run(
        ["git", "-C", str(root), "merge-base", "--is-ancestor", ancestor, descendant],
        capture_output=True,
        text=True,
    )
    return result.returncode == 0


def _branches(root: Path) -> list[str]:
    return _git(root, "branch", "--format=%(refname:short)").split()


def _seed_repo(path: Path, name: str) -> tuple[Path, Path]:
    """Repo temporal con ``main``, ``develop`` y workspace Taiki (sin remoto)."""
    root = git_repo(path)
    _commit(root, ".gitignore", ".worktrees/\n", "chore: ignora worktrees")
    _commit(root, "app.py", "v0\n", "chore: base")
    _git(root, "branch", "-m", "main")
    _git(root, "checkout", "-b", "develop")
    info = workspace.init_workspace(root, name=name)
    return root, Path(info["db"])


def _claim_worktree(
    h, root: Path, database: Path, title: str
) -> tuple[str, Path, int]:
    """Crea el ticket y lo reclama con worktree, como un worker real."""
    with h.env(TAIKI_DB=str(database)):
        conn = db.connect()
        try:
            code = repo.create_ticket(conn, title=title, project="flujo")["code"]
        finally:
            conn.close()
    with h.env(TAIKI_DB=str(database)), contextlib.chdir(root), \
            contextlib.redirect_stdout(io.StringIO()), \
            contextlib.redirect_stderr(io.StringIO()):
        claimed = cmd_claim(
            Namespace(
                code=code, agent="worker", force=False, steal=False, worktree=True
            )
        )
    return code, root / ".worktrees" / code, claimed


def _merge_code(h, root: Path, database: Path, code: str) -> int:
    with h.env(TAIKI_DB=str(database)), contextlib.chdir(root), \
            contextlib.redirect_stdout(io.StringIO()), \
            contextlib.redirect_stderr(io.StringIO()):
        return cmd_worktree_merge(
            Namespace(code=code, path=None, delete=True, no_tests=True, json=True)
        )


def _confirm(h, database: Path, code: str) -> None:
    """Cierra el trámite: completado → verificado (conductor) → confirmado."""
    with h.env(TAIKI_DB=str(database)):
        conn = db.connect()
        try:
            repo.complete_ticket(conn, code, resolution="E2E listo")
            repo.transition(
                conn, code, "verificado", note="gates E2E verdes", actor="conductor"
            )
            repo.transition(conn, code, "confirmado", actor="usuario", actor_type="usuario")
        finally:
            conn.close()


def _flow(h) -> tuple[dict, dict]:
    """E2E: claim con worktree → commit agent → merge develop → release → main."""
    tmp = Path(h.tmp)

    # ── Ciclo completo sobre develop ─────────────────────────────────────
    root, database = _seed_repo(tmp / "flujo", "flujo")
    main_head = _git(root, "rev-parse", "main")
    code, tree, claim_rc = _claim_worktree(h, root, database, "Flujo E2E")
    branch = _git(tree, "branch", "--show-current")
    # develop avanza después de crear el worktree: el merge debe rebasar.
    _commit(root, "otro.txt", "otro\n", "chore: avance en develop (T-0099)")
    _commit(tree, "flujo.txt", "flujo\n", "feat: flujo E2E (T-0001)")
    develop_before = _git(root, "rev-parse", "develop")

    _confirm(h, database, code)
    merge_rc = _merge_code(h, root, database, code)
    develop_after = _git(root, "rev-parse", "develop")
    main_after_merge = _git(root, "rev-parse", "main")
    branches = _branches(root)

    release_rc, release = _release(root, no_verify=False, verify_cmd="true")
    tag = str(release.get("tag"))
    main_commit = _git(root, "rev-parse", "main")
    message = _git(root, "log", "-1", "--format=%B", "main")
    changelog = (root / "CHANGELOG.md").read_text(encoding="utf-8")
    subjects = _git(root, "log", "--format=%s", "develop")
    develop_reconciled = _is_ancestor(root, "main", "develop")
    restored_branch = _git(root, "branch", "--show-current")

    # ── Segundo ciclo: sin reconciliación manual la release fallaría ─────
    _commit(root, "segundo.txt", "dos\n", "feat: segundo ciclo (T-0005)")
    second_rc, second = _release(root, no_verify=False, verify_cmd="true")
    second_tag = str(second.get("tag"))
    main_commits = int(_git(root, "rev-list", "--count", f"{main_head}..main"))
    main_merges = _git(root, "rev-list", "--merges", "main")
    develop_merges = _git(root, "log", "--merges", "--format=%s", "develop").splitlines()
    release_tags = _git(root, "tag", "--list", "v*").splitlines()

    # ── Conflicto en rebase: aborta sin tocar develop ni main ───────────
    conflict, conflict_db = _seed_repo(tmp / "conflicto-flujo", "conflicto")
    conflict_main = _git(conflict, "rev-parse", "main")
    code2, tree2, claim2_rc = _claim_worktree(h, conflict, conflict_db, "Conflicto")
    branch2 = _git(tree2, "branch", "--show-current")
    _commit(tree2, "app.py", "worktree\n", "feat: rama (T-0002)")
    _commit(conflict, "app.py", "develop\n", "fix: develop (T-0003)")
    conflict_develop = _git(conflict, "rev-parse", "develop")
    _confirm(h, conflict_db, code2)
    conflict_rc = _merge_code(h, conflict, conflict_db, code2)

    gitdir = Path(_git(tree2, "rev-parse", "--git-dir"))
    if not gitdir.is_absolute():
        gitdir = tree2 / gitdir
    rebase_aborted = not (gitdir / "rebase-merge").exists() and not (
        gitdir / "rebase-apply"
    ).exists()

    # ── Gates rojos: la release no toca main ────────────────────────────
    red_rc, _ = _release(conflict, no_verify=False, verify_cmd="false")

    actual = {
        "claim_rc": claim_rc,
        "agent_branch": branch.startswith(f"agent/{code}"),
        "agent_commit_in_develop": "feat: flujo E2E (T-0001)" in subjects,
        "develop_advanced": develop_after != develop_before
        and _is_ancestor(root, develop_before, develop_after),
        "develop_reconciled": develop_reconciled and restored_branch == "develop",
        "develop_merges": develop_merges,
        "merge_kept_main": main_after_merge == main_head,
        "worktree_removed": not tree.exists(),
        "branch_deleted": branch not in branches,
        "release_rc": release_rc,
        "release_branch": release.get("branch"),
        "release_squashed": release.get("commits"),
        "release_reconciled": release.get("reconciled"),
        "second_rc": second_rc,
        "second_tag": second_tag,
        "second_reconciled": second.get("reconciled"),
        "main_commits": main_commits,
        "main_linear": main_merges == "",
        "release_lineage": _git(root, "rev-parse", "main^^"),
        "tags": release_tags,
        "tag_type": _git(root, "cat-file", "-t", tag) if tag else None,
        "tag_on_main": _git(root, "rev-parse", f"{tag}^{{commit}}") if tag else None,
        "second_tag_on_main": (
            _git(root, "rev-parse", f"{second_tag}^{{commit}}") if second_tag else None
        ),
        "release_message": all(
            part in message for part in ("chore(release)", "### feat", "T-0001")
        ),
        "changelog": all(
            part in changelog
            for part in ("## v0.1.0", "### feat", "T-0001", "flujo E2E")
        ),
        "main_has_files": (root / "flujo.txt").is_file()
        and (root / "otro.txt").is_file(),
        "conflict_claim_rc": claim2_rc,
        "conflict_rc": conflict_rc,
        "conflict_develop": _git(conflict, "rev-parse", "develop")
        == conflict_develop,
        "conflict_main": _git(conflict, "rev-parse", "main") == conflict_main,
        "conflict_worktree": tree2.exists(),
        "conflict_branch": branch2 in _branches(conflict),
        "conflict_rebase_aborted": rebase_aborted,
        "red_rc": red_rc,
        "red_main": _git(conflict, "rev-parse", "main") == conflict_main,
        "red_tag": _tag_exists(conflict, "v0.1.0"),
        "red_changelog": (conflict / "CHANGELOG.md").exists(),
    }
    expected = {
        "claim_rc": 0,
        "agent_branch": True,
        "agent_commit_in_develop": True,
        "develop_advanced": True,
        "develop_reconciled": True,
        "develop_merges": [
            "chore: reconciliar develop tras v0.2.0",
            "chore: reconciliar develop tras v0.1.0",
        ],
        "merge_kept_main": True,
        "worktree_removed": True,
        "branch_deleted": True,
        "release_rc": 0,
        "release_branch": "develop",
        "release_squashed": 2,
        "release_reconciled": True,
        "second_rc": 0,
        "second_tag": "v0.2.0",
        "second_reconciled": True,
        "main_commits": 2,
        "main_linear": True,
        "release_lineage": main_head,
        "tags": ["v0.1.0", "v0.2.0"],
        "tag_type": "tag",
        "tag_on_main": main_commit,
        "second_tag_on_main": _git(root, "rev-parse", "main") if second_tag else None,
        "release_message": True,
        "changelog": True,
        "main_has_files": True,
        "conflict_claim_rc": 0,
        "conflict_rc": 1,
        "conflict_develop": True,
        "conflict_main": True,
        "conflict_worktree": True,
        "conflict_branch": True,
        "conflict_rebase_aborted": True,
        "red_rc": 1,
        "red_main": True,
        "red_tag": False,
        "red_changelog": False,
    }
    return actual, expected


def run(h) -> None:
    tmp = Path(h.tmp)
    root = git_repo(tmp / "proyecto")
    _commit(root, "app.py", "v0\n", "chore: base")
    _git(root, "branch", "-m", "main")
    main_head = _git(root, "rev-parse", "main")
    _git(root, "checkout", "-b", "develop")
    _commit(root, "saludo.py", "hola\n", "feat: añade saludo (T-0001)")
    _commit(root, "saludo.py", "hola!\n", "fix: corrige saludo (T-0002)")
    develop_head = _git(root, "rev-parse", "develop")

    dry_rc, dry = _release(root, dry_run=True)
    dry_head = _git(root, "rev-parse", "main")
    dry_tag = _tag_exists(root, "v0.1.0")

    red_rc, _ = _release(root, no_verify=False, verify_cmd="false")
    red_head = _git(root, "rev-parse", "main")
    red_tag = _tag_exists(root, "v0.1.0")

    conflict = git_repo(tmp / "conflicto")
    _commit(conflict, "a.txt", "a\n", "chore: base")
    _git(conflict, "branch", "-m", "main")
    _git(conflict, "checkout", "-b", "develop")
    _commit(conflict, "b.txt", "b\n", "feat: rama (T-0003)")
    _git(conflict, "checkout", "main")
    _commit(conflict, "c.txt", "c\n", "fix: main diverge (T-0004)")
    conflict_rc, conflict_payload = _release(conflict, dry_run=True)

    negative = {
        "dry_rc": dry_rc,
        "dry_tag": dry.get("tag"),
        "dry_head": dry_head,
        "dry_wrote_tag": dry_tag,
        "red_rc": red_rc,
        "red_head": red_head,
        "red_wrote_tag": red_tag,
        "conflict_rc": conflict_rc,
        "conflict_wrote": bool(conflict_payload),
    }
    expected_negative = {
        "dry_rc": 0,
        "dry_tag": "v0.1.0",
        "dry_head": main_head,
        "dry_wrote_tag": False,
        "red_rc": 1,
        "red_head": main_head,
        "red_wrote_tag": False,
        "conflict_rc": 1,
        "conflict_wrote": False,
    }

    rc, release = _release(root, no_verify=False, verify_cmd="true")
    message = _git(root, "log", "-1", "--format=%B", "main")
    changelog = (root / "CHANGELOG.md").read_text(encoding="utf-8")
    flow_actual: dict = {}
    flow_expected: dict = {}
    if os.environ.get("TAIKI_E2E", "").strip().lower() in ("1", "true", "yes"):
        flow_actual, flow_expected = _flow(h)
    h.eq(
        {
            **negative,
            "release": {
                "rc": rc,
                "main_commits": int(
                    _git(root, "rev-list", "--count", f"{main_head}..main")
                ),
                "develop_reconciled": _is_ancestor(root, "main", "develop"),
                "develop_moved": _git(root, "rev-parse", "develop") != develop_head,
                "branch": release.get("branch"),
                "reconciled": release.get("reconciled"),
                "reconcile_command": bool(release.get("reconcile_command")),
                "tag_type": _git(root, "cat-file", "-t", "v0.1.0"),
                "tag": release.get("tag"),
                "version": release.get("version"),
                "tickets": release.get("tickets"),
                "message_groups": all(
                    part in message
                    for part in ("### feat", "### fix", "T-0001", "T-0002")
                ),
                "changelog": all(
                    part in changelog
                    for part in ("## v0.1.0", "### feat", "T-0001", "T-0002")
                ),
            },
            "flow": flow_actual,
        },
        {
            **expected_negative,
            "release": {
                "rc": 0,
                "main_commits": 1,
                "develop_reconciled": True,
                "develop_moved": True,
                "branch": "develop",
                "reconciled": True,
                "reconcile_command": True,
                "tag_type": "tag",
                "tag": "v0.1.0",
                "version": "0.1.0",
                "tickets": ["T-0001", "T-0002"],
                "message_groups": True,
                "changelog": True,
            },
            "flow": flow_expected,
        },
        "dry-run/gates rojos no escriben, el release aplana y el E2E agent→develop→main cierra el ciclo",
    )
