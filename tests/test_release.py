"""``taiki release``: squash de develop en main con gates, tag y CHANGELOG.

El techo de la suite es 1051 checks, así que todas las invariantes se consolidan
en un único ``h.eq`` con un diccionario de observaciones: rutas negativas
(dry-run, gates rojos y ancestro) y release real.
"""

from __future__ import annotations

import contextlib
import io
import json
import subprocess
from argparse import Namespace
from pathlib import Path

from commands.release import cmd_release
from tests.seeds import git_repo


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
    message = _git(root, "log", "-1", "--format=%B")
    changelog = (root / "CHANGELOG.md").read_text(encoding="utf-8")
    h.eq(
        {
            **negative,
            "rc": rc,
            "main_commits": int(
                _git(root, "rev-list", "--count", f"{main_head}..main")
            ),
            "develop_head": _git(root, "rev-parse", "develop"),
            "tag_type": _git(root, "cat-file", "-t", "v0.1.0"),
            "release_tag": release.get("tag"),
            "release_version": release.get("version"),
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
        {
            **expected_negative,
            "rc": 0,
            "main_commits": 1,
            "develop_head": develop_head,
            "tag_type": "tag",
            "release_tag": "v0.1.0",
            "release_version": "0.1.0",
            "tickets": ["T-0001", "T-0002"],
            "message_groups": True,
            "changelog": True,
        },
        "dry-run/gates rojos no escriben, el ancestro se exige y el release aplana, etiqueta y documenta",
    )
