"""``taiki release`` y el flujo agent → develop → release → main.

El techo de la suite es 1051 checks, así que todas las invariantes se consolidan
en un único ``h.eq`` con un diccionario de observaciones: rutas negativas
(dry-run, gates rojos y ancestro), sync, marcador de releases y release real.

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
import re
import subprocess
import sys
import threading
import tomllib
from argparse import Namespace
from pathlib import Path

from commands.release import (
    _bump_for,
    _changelog,
    _explicit_bumps,
    _groups,
    _tickets,
    cmd_release,
)
from commands.tickets import cmd_claim
from commands.worktree import cmd_worktree_merge
from tests.seeds import git_repo
from ticketlib import __version__, db, repo, workspace

# CHANGELOG de partida con el bloque de trabajo en curso que dejaban las
# integraciones antiguas y una release anterior que debe conservarse.
_CHANGELOG_SEED = """# Changelog

## Unreleased

Tickets: T-0045

### feat
- feat: bloque viejo (T-0045)

## v0.2.0 — 2026-11-02

Tickets: T-0038

### feat
- feat: release anterior (T-0038)
"""


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
        allow_ahead=options.get("allow_ahead", False),
        push=options.get("push", False),
        verify_cmd=options.get("verify_cmd"),
        json=True,
    )
    output = io.StringIO()
    with contextlib.redirect_stdout(output), \
            contextlib.redirect_stderr(io.StringIO()):
        code = cmd_release(args)
    raw = output.getvalue().strip()
    return code, (json.loads(raw) if raw else {})


def _release_error(root: Path, **options) -> tuple[int, str]:
    """Ejecuta ``taiki release`` y devuelve el código y el stderr."""
    args = Namespace(
        repo=str(root),
        integration=None,
        bump=options.get("bump"),
        dry_run=options.get("dry_run", False),
        no_verify=options.get("no_verify", True),
        allow_ahead=options.get("allow_ahead", False),
        push=options.get("push", False),
        verify_cmd=options.get("verify_cmd"),
        json=True,
    )
    error = io.StringIO()
    with contextlib.redirect_stdout(io.StringIO()), \
            contextlib.redirect_stderr(error):
        code = cmd_release(args)
    return code, error.getvalue().strip()


def _release_gate_reads_stdin(root: Path, tmp: Path, seconds: float = 5.0) -> bool:
    """``release`` con un gate que lee stdin no se cuelga.

    El stdin del test se sustituye por una tubería sin datos: si el gate no
    recibe ``DEVNULL`` se queda leyendo y el hilo no termina. Al cerrar la
    tubería llega EOF y la suite nunca se cuelga esperando al hijo. Devuelve
    ``True`` solo si el release terminó dentro del plazo y con éxito.
    """
    gate = tmp / "gate_stdin.py"
    gate.write_text(
        "import sys\nsys.stdin.read()\nprint('gate-ok')\n", encoding="utf-8"
    )
    read_fd, write_fd = os.pipe()
    saved_fd = os.dup(0)
    os.dup2(read_fd, 0)
    os.close(read_fd)
    box: dict[str, tuple[int, dict]] = {}

    def target() -> None:
        box["result"] = _release(
            root, no_verify=False, verify_cmd=[sys.executable, str(gate)]
        )

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
    return finished and box.get("result", (1, {}))[0] == 0


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
    _seed_versions(root)
    _commit(root, ".gitignore", ".worktrees/\n", "chore: ignora worktrees")
    _commit(root, "app.py", "v0\n", "chore: base")
    _git(root, "branch", "-m", "main")
    _git(root, "checkout", "-b", "develop")
    info = workspace.init_workspace(root, name=name)
    return root, Path(info["db"])


def _sync_repo(path: Path, name: str) -> tuple[Path, Path]:
    """Repo con ``develop`` publicado en un bare local: upstream sin red.

    No necesita workspace Taiki (solo se prueban los errores de sync), así que
    evita la DB y los hooks de ``_seed_repo``.
    """
    root = git_repo(path)
    _commit(root, "app.py", "v0\n", "chore: base")
    _git(root, "branch", "-m", "main")
    _git(root, "checkout", "-b", "develop")
    bare = path.parent / f"{path.name}-remoto.git"
    subprocess.run(["git", "init", "-q", "--bare", str(bare)], check=True)
    _git(root, "remote", "add", "origin", str(bare))
    _git(root, "push", "-q", "-u", "origin", "develop")
    return root, bare


def _sync_cases(tmp: Path) -> tuple[dict, dict]:
    """Casos ahead/behind/divergencia y ``--allow-ahead`` en un solo repo."""
    root, bare = _sync_repo(tmp / "sync", "sync")
    main_before = _git(root, "rev-parse", "main")
    remote_before = _git(bare, "rev-parse", "refs/heads/develop")
    # ahead: un commit local y upstream intacto.
    _commit(root, "local1.txt", "local\n", "feat: local uno (T-0038)")
    ahead_rc, ahead_err = _release_error(root, dry_run=True)
    allow_rc, allow = _release(root, dry_run=True, allow_ahead=True)
    # behind: el remoto tiene ese commit y la rama local vuelve atrás. Basta
    # con mover la ref de seguimiento local: la release no consulta el bare.
    _git(root, "update-ref", "refs/remotes/origin/develop", "HEAD")
    _git(root, "reset", "--hard", "HEAD~1")
    behind_rc, behind_err = _release_error(root, dry_run=True)
    # Con --push el behind también aborta, y --push --dry-run es incompatible.
    push_behind_rc, push_behind_err = _release_error(root, push=True)
    incompatible_rc, incompatible_err = _release_error(
        root, push=True, dry_run=True
    )
    push_behind_clean = (
        _git(root, "rev-parse", "main") == main_before
        and not _tag_exists(root, "v0.1.0")
        and _git(bare, "rev-parse", "refs/heads/develop") == remote_before
        and not _has_ref(bare, "refs/heads/main")
    )
    # divergencia: la rama local y el upstream avanzan por su cuenta.
    _commit(root, "local2.txt", "local\n", "feat: local dos (T-0039)")
    diverged_rc, diverged_err = _release_error(root, dry_run=True)

    actual = {
        "ahead_rc": ahead_rc,
        "ahead_push": "git push origin develop" in ahead_err,
        "ahead_names": "develop" in ahead_err and "origin/develop" in ahead_err,
        "behind_rc": behind_rc,
        "behind_pull": "git pull --ff-only" in behind_err,
        "behind_names": "develop" in behind_err and "origin/develop" in behind_err,
        "push_behind_rc": push_behind_rc,
        "push_behind_text": "por detrás" in push_behind_err
        and "git pull --ff-only" in push_behind_err,
        "push_behind_clean": push_behind_clean,
        "incompatible_rc": incompatible_rc,
        "incompatible": "incompatibles" in incompatible_err,
        "diverged_rc": diverged_rc,
        "diverged_text": "diverg" in diverged_err.lower(),
        "allow_rc": allow_rc,
        "allow_flag": allow.get("allow_ahead"),
        "allow_tag": allow.get("tag"),
    }
    expected = {
        "ahead_rc": 1,
        "ahead_push": True,
        "ahead_names": True,
        "behind_rc": 1,
        "behind_pull": True,
        "behind_names": True,
        "push_behind_rc": 1,
        "push_behind_text": True,
        "push_behind_clean": True,
        "incompatible_rc": 1,
        "incompatible": True,
        "diverged_rc": 1,
        "diverged_text": True,
        "allow_rc": 0,
        "allow_flag": True,
        "allow_tag": "v0.1.0",
    }
    return actual, expected


def _has_ref(root: Path, ref: str) -> bool:
    result = subprocess.run(
        ["git", "-C", str(root), "rev-parse", "--verify", "--quiet", ref],
        capture_output=True,
        text=True,
    )
    return result.returncode == 0


def _seed_versions(root: Path, version: str = "0.0.0") -> None:
    """Ficheros de versión de partida que ``taiki release`` debe alinear."""
    (root / "ticketlib").mkdir(exist_ok=True)
    _commit(
        root,
        "ticketlib/__init__.py",
        f'"""Sistema de tickets local para agentes y usuario."""\n\n'
        f'__version__ = "{version}"\n',
        "chore: versión del paquete",
    )
    _commit(
        root,
        "pyproject.toml",
        f'[project]\nname = "taiki"\nversion = "{version}"\n',
        "chore: metadatos del paquete",
    )


def _parse_versions(
    init_text: str | None, project_text: str | None
) -> dict[str, str | None]:
    init_version = None
    if init_text:
        match = re.search(r'^__version__ = "([^"]+)"', init_text, re.MULTILINE)
        init_version = match.group(1) if match else None
    project_version = None
    if project_text:
        project_version = tomllib.loads(project_text).get("project", {}).get("version")
    return {"init": init_version, "pyproject": project_version}


def _version_text(root: Path) -> dict[str, str | None]:
    paths = {
        "init": root / "ticketlib" / "__init__.py",
        "pyproject": root / "pyproject.toml",
    }
    return {
        key: path.read_text(encoding="utf-8") if path.is_file() else None
        for key, path in paths.items()
    }


def _version_files(root: Path) -> dict[str, str | None]:
    text = _version_text(root)
    return _parse_versions(text["init"], text["pyproject"])


def _version_files_at(root: Path, ref: str) -> dict[str, str | None]:
    def show(path: str) -> str | None:
        result = subprocess.run(
            ["git", "-C", str(root), "show", f"{ref}:{path}"],
            capture_output=True,
            text=True,
        )
        return result.stdout if result.returncode == 0 else None

    return _parse_versions(show("ticketlib/__init__.py"), show("pyproject.toml"))


def _latest_tag(root: Path) -> str | None:
    result = subprocess.run(
        ["git", "-C", str(root), "tag", "--list", "v[0-9]*", "--sort=-v:refname"],
        capture_output=True,
        text=True,
    )
    tags = result.stdout.split() if result.returncode == 0 else []
    return tags[0] if tags else None


def _version_repo(path: Path, version: str = "0.0.0") -> Path:
    """Repo ``main``/``develop`` con versión, CHANGELOG y app de partida."""
    root = git_repo(path)
    _seed_versions(root, version)
    _commit(root, "CHANGELOG.md", "# Changelog\n", "docs: changelog base")
    _commit(root, "app.py", "v0\n", "chore: base")
    _git(root, "branch", "-m", "main")
    _git(root, "checkout", "-b", "develop")
    return root


def _merge_in_progress(root: Path) -> bool:
    result = subprocess.run(
        ["git", "-C", str(root), "rev-parse", "--verify", "--quiet", "MERGE_HEAD"],
        capture_output=True,
        text=True,
    )
    return result.returncode == 0


def _marker_at(root: Path, ref: str) -> list[dict]:
    return json.loads(_git(root, "show", f"{ref}:.taiki/releases.json"))


def _reconcile_cases(tmp: Path) -> tuple[dict, dict]:
    """Reconciliación: conflicto de versión, ajeno, marcador y rango.

    Un solo repo encadena los dos conflictos: primero ``develop`` mueve los
    ficheros de versión al margen del release (se resuelve con la versión de
    ``main``); después borra ``CHANGELOG.md`` (modify/delete no se resuelve y
    aborta). El marcador debe sobrevivir en ``main`` incluso en el fallo.
    """
    root = _version_repo(tmp / "reconciliacion")
    init_content = '"""Sistema de tickets."""\n\n__version__ = "0.0.5"\n'
    project_content = '[project]\nname = "taiki"\nversion = "0.0.5"\n'
    _commit(
        root,
        "ticketlib/__init__.py",
        init_content,
        "chore: versión de develop (T-0060)",
    )
    _commit(
        root,
        "pyproject.toml",
        project_content,
        "chore: metadatos de develop (T-0060)",
    )
    _commit(root, "feature.txt", "feature\n", "feat: feature (T-0061)")

    rc, first = _release(root, no_verify=True)
    tag = str(first.get("tag"))
    develop_versions = _version_files(root)
    tag_versions = _version_files_at(root, tag)
    marker_develop = _marker_at(root, "develop")
    marker_main = _marker_at(root, "main")

    _commit(root, "siguiente.txt", "siguiente\n", "fix: parche (T-0062)")
    _, following = _release(root, dry_run=True)

    _git(root, "rm", "CHANGELOG.md")
    _git(root, "commit", "-m", "fix: sin changelog (T-0063)")
    before_foreign = _git(root, "rev-parse", "develop")
    foreign_rc, foreign = _release(root, no_verify=True)
    foreign_tag = str(foreign.get("tag"))
    foreign_marker = _marker_at(root, "main")

    actual = {
        "rc": rc,
        "reconciled": first.get("reconciled"),
        "error": first.get("reconcile_error"),
        "resolved": sorted(first.get("resolved_conflicts") or []),
        "merge_head": _merge_in_progress(root),
        "develop_versions": develop_versions,
        "tag_versions": tag_versions,
        "marker_develop": [entry.get("tag") for entry in marker_develop],
        "marker_main": [entry.get("tag") for entry in marker_main],
        "next_range": following.get("range"),
        "next_tickets": following.get("tickets"),
        "foreign_rc": foreign_rc,
        "foreign_error": bool(foreign.get("reconcile_error")),
        "foreign_develop": _git(root, "rev-parse", "develop") == before_foreign,
        "foreign_merge_head": _merge_in_progress(root),
        "foreign_tag": _tag_exists(root, foreign_tag),
        "foreign_marker": [entry.get("tag") for entry in foreign_marker],
    }
    expected = {
        "rc": 0,
        "reconciled": True,
        "error": None,
        "resolved": ["pyproject.toml", "ticketlib/__init__.py"],
        "merge_head": False,
        "develop_versions": {"init": "0.1.0", "pyproject": "0.1.0"},
        "tag_versions": {"init": "0.1.0", "pyproject": "0.1.0"},
        "marker_develop": ["v0.1.0"],
        "marker_main": ["v0.1.0"],
        "next_range": first.get("released_sha"),
        "next_tickets": ["T-0062"],
        "foreign_rc": 0,
        "foreign_error": True,
        "foreign_develop": True,
        "foreign_merge_head": False,
        "foreign_tag": True,
        "foreign_marker": ["v0.1.0", foreign_tag],
    }
    return actual, expected


def _push_cases(tmp: Path) -> tuple[dict, dict]:
    """``--push`` contra un bare local: éxito, ahead y rechazo de main.

    Un solo repo con remoto para el éxito y el rechazo; los abortos previos
    (behind y ``--dry-run``) se prueban en ``_sync_cases``. Sin red.
    """
    root, bare = _sync_repo(tmp / "push", "push")
    _commit(root, "nuevo.txt", "nuevo\n", "feat: publica release (T-0040)")
    rc, result = _release(root, push=True)
    tag = str(result.get("tag"))
    main_sha = _git(root, "rev-parse", "main")
    develop_sha = _git(root, "rev-parse", "develop")
    remote_develop_ok = _git(bare, "rev-parse", "refs/heads/develop")
    remote_main_ok = _git(bare, "rev-parse", "refs/heads/main")
    remote_tag_ok = _git(bare, "rev-parse", f"refs/tags/{tag}^{{commit}}")
    remote_tag_type_ok = _git(bare, "cat-file", "-t", f"refs/tags/{tag}")

    # El remoto rechaza main (protección): no se publica el tag ni se reconcilia.
    hook = bare / "hooks" / "pre-receive"
    hook.write_text(
        "#!/bin/sh\nwhile read old new ref; do\n"
        '  case "$ref" in refs/heads/main) exit 1;; esac\n'
        "done\nexit 0\n",
        encoding="utf-8",
    )
    hook.chmod(0o755)
    _commit(root, "posterior.txt", "posterior\n", "fix: segundo parche (T-0041)")
    before_reject = _git(root, "rev-parse", "develop")
    reject_rc, reject_err = _release_error(root, push=True)
    rejected_tag = "v0.1.1"

    actual = {
        "rc": rc,
        "pushed": result.get("pushed"),
        "remote": result.get("remote"),
        "remote_develop": remote_develop_ok,
        "remote_main": remote_main_ok,
        "remote_tag": remote_tag_ok,
        "remote_tag_type": remote_tag_type_ok,
        "local_main": main_sha,
        "develop_after": develop_sha,
        "reconciled": result.get("reconciled"),
        "branch": result.get("branch"),
        "reject_rc": reject_rc,
        "reject_step": "Paso: push de main" in reject_err,
        "reject_retry": "Reintenta: git push --atomic origin main" in reject_err,
        "reject_develop": _git(root, "rev-parse", "develop"),
        "reject_before": before_reject,
        "reject_remote_main": _git(bare, "rev-parse", "refs/heads/main"),
        "reject_remote_tag": _has_ref(bare, f"refs/tags/{rejected_tag}"),
        "reject_local_tag": _tag_exists(root, rejected_tag),
        "reject_unreconciled": not _is_ancestor(root, "main", "develop"),
    }
    expected = {
        "rc": 0,
        "pushed": ["develop", "main", "v0.1.0", "develop-reconcile"],
        "remote": "origin",
        "remote_develop": develop_sha,
        "remote_main": main_sha,
        "remote_tag": main_sha,
        "remote_tag_type": "tag",
        "local_main": main_sha,
        "develop_after": develop_sha,
        "reconciled": True,
        "branch": "develop",
        "reject_rc": 1,
        "reject_step": True,
        "reject_retry": True,
        "reject_develop": before_reject,
        "reject_before": before_reject,
        "reject_remote_main": main_sha,
        "reject_remote_tag": False,
        "reject_local_tag": True,
        "reject_unreconciled": True,
    }
    return actual, expected


def _bump_cases(root: Path) -> dict[str, dict]:
    """Niveles del trailer: capa pura y dos dry-runs de integración.

    La precedencia y el parseo se prueban en ``_bump_for``/``_explicit_bumps``
    (sin repos); el repo de ``run`` solo se usa para comprobar que el resumen
    integra ``explicit_bumps`` y que ``--bump`` prevalece. Un ``reset --hard``
    devuelve ``develop`` a su base.
    """

    def commit(subject: str, trailer: str = "") -> dict[str, str]:
        body = f"{subject}\n\n{trailer}" if trailer else subject
        return {"sha": subject[:7], "subject": subject, "body": body}

    cases = {
        "trailer_patch": [commit("feat: flag (T-0010)", "Release-Bump: patch")],
        "trailer_minor": [
            commit("feat: flag (T-0011)", "Release-Bump: minor"),
            commit("fix: borde (T-0012)"),
        ],
        "breaking_trailer_patch": [
            commit("feat!: rompe api (T-0013)", "Release-Bump: patch")
        ],
        "convention": [commit("feat: normal (T-0015)")],
    }
    results: dict[str, dict] = {
        name: {
            "bump": _bump_for(commits),
            "explicit": [
                (item["subject"], item["level"])
                for item in _explicit_bumps(commits)
            ],
        }
        for name, commits in cases.items()
    }
    base = _git(root, "rev-parse", "develop")
    _commit(
        root,
        "sondeo-bump.txt",
        "sondeo\n",
        "feat: flag (T-0014)\n\nRelease-Bump: patch",
    )
    _, declared = _release(root, dry_run=True)
    _, override = _release(root, dry_run=True, bump="minor")
    _git(root, "reset", "--hard", base)
    results["integration"] = {
        "bump": declared.get("bump"),
        "explicit": [
            (item.get("subject"), item.get("level"))
            for item in declared.get("explicit_bumps") or []
        ],
        "override": override.get("bump"),
    }
    return results


def _changelog_cases() -> tuple[dict, dict]:
    """``_changelog`` consume ``Unreleased``, no duplica y conserva el histórico.

    Simula la release v0.3.0 desde v0.2.0 con los tickets T-0050–T-0057 en la
    capa pura: sin repo, sin red y sin escribir ficheros.
    """
    kinds = ("feat", "fix", "feat", "docs", "fix", "feat", "test", "refactor")
    commits = [
        {
            "sha": f"{index:07d}",
            "subject": f"{kind}: cambio {index} (T-00{49 + index})",
            "body": f"{kind}: cambio {index} (T-00{49 + index})",
        }
        for index, kind in enumerate(kinds, start=1)
    ]
    tickets = _tickets(commits)
    groups = _groups(commits)
    generated = _changelog(_CHANGELOG_SEED, "0.3.0", tickets, groups)
    repeated = _changelog(generated, "0.3.0", tickets, groups)
    newest = generated.split("## v0.3.0", 1)[-1].split("## v0.2.0", 1)[0]
    actual = {
        "unreleased": "Unreleased" not in generated,
        "old_block": "T-0045" not in generated,
        "version_once": generated.count("## v0.3.0") == 1,
        "previous": "## v0.2.0" in generated,
        "previous_body": "release anterior (T-0038)" in generated,
        "ordered": generated.index("## v0.3.0") < generated.index("## v0.2.0"),
        "range": sorted(code for code in tickets if code >= "T-0050"),
        "range_in_section": all(
            f"T-{code:04d}" in newest for code in range(50, 58)
        ),
        "rerun": repeated.count("## v0.3.0") == 1
        and "Unreleased" not in repeated
        and "## v0.2.0" in repeated,
    }
    expected = {
        "unreleased": True,
        "old_block": True,
        "version_once": True,
        "previous": True,
        "previous_body": True,
        "ordered": True,
        "range": [f"T-{code:04d}" for code in range(50, 58)],
        "range_in_section": True,
        "rerun": True,
    }
    return actual, expected


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
    subjects = _git(root, "log", "--format=%s", "develop")
    develop_reconciled = _is_ancestor(root, "main", "develop")
    restored_branch = _git(root, "branch", "--show-current")

    # El marcador viaja en el merge de reconciliación (sin commit extra).
    marker = json.loads(
        (root / ".taiki" / "releases.json").read_text(encoding="utf-8")
    )
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
        "marker_entries": len(marker),
        "marker_tags": [entry.get("tag") for entry in marker],
        "marker_first_sha": marker[0].get("released_sha") if marker else None,
        "main_commits": main_commits,
        "main_linear": main_merges == "",
        "release_lineage": _git(root, "rev-parse", "main^"),
        "tags": release_tags,
        "tag_type": _git(root, "cat-file", "-t", tag) if tag else None,
        "tag_on_main": _git(root, "rev-parse", f"{tag}^{{commit}}") if tag else None,
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
        "develop_merges": ["chore: reconciliar develop tras v0.1.0"],
        "merge_kept_main": True,
        "worktree_removed": True,
        "branch_deleted": True,
        "release_rc": 0,
        "release_branch": "develop",
        "release_squashed": 2,
        "release_reconciled": True,
        "marker_entries": 1,
        "marker_tags": ["v0.1.0"],
        "marker_first_sha": develop_after,
        "main_commits": 1,
        "main_linear": True,
        "release_lineage": main_head,
        "tags": ["v0.1.0"],
        "tag_type": "tag",
        "tag_on_main": main_commit,
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
    _seed_versions(root)
    _commit(root, "app.py", "v0\n", "chore: base")
    _commit(
        root,
        "CHANGELOG.md",
        _CHANGELOG_SEED,
        "docs: changelog con bloque Unreleased",
    )
    _git(root, "branch", "-m", "main")
    main_head = _git(root, "rev-parse", "main")
    _git(root, "checkout", "-b", "develop")
    _commit(root, "saludo.py", "hola\n", "feat: añade saludo (T-0001)")
    _commit(root, "saludo.py", "hola!\n", "fix: corrige saludo (T-0002)")
    develop_head = _git(root, "rev-parse", "develop")

    before_dry = _version_text(root)
    dry_rc, dry = _release(root, dry_run=True)
    dry_unchanged = _version_text(root) == before_dry
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

    # Un gate que lee stdin no debe colgar la release si hay un TTY delante.
    stdin_root = git_repo(tmp / "stdin-gate")
    _seed_versions(stdin_root)
    _commit(stdin_root, "app.py", "v0\n", "chore: base")
    _git(stdin_root, "branch", "-m", "main")
    _git(stdin_root, "checkout", "-b", "develop")
    _commit(stdin_root, "gate.txt", "gate\n", "feat: gate con stdin (T-0006)")
    stdin_gate = _release_gate_reads_stdin(stdin_root, tmp)

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
        "stdin_gate": stdin_gate,
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
        "stdin_gate": True,
    }

    sync_actual, sync_expected = _sync_cases(tmp)
    push_actual, push_expected = _push_cases(tmp)
    bumps_actual = _bump_cases(root)
    changelog_units, changelog_unit_expected = _changelog_cases()
    reconcile_actual, reconcile_expected = _reconcile_cases(tmp)

    rc, release = _release(root, no_verify=False, verify_cmd="true")
    message = _git(root, "log", "-1", "--format=%B", "main")
    changelog_first = (root / "CHANGELOG.md").read_text(encoding="utf-8")
    first_develop_moved = _git(root, "rev-parse", "develop") != develop_head
    first_versions = _version_files(root)
    first_tag_versions = _version_files_at(root, "v0.1.0")
    marker_path = root / ".taiki" / "releases.json"
    first_entries = json.loads(marker_path.read_text(encoding="utf-8"))
    first_tracked = _git(root, "ls-files", "--", ".taiki/releases.json")

    _commit(root, "posterior.txt", "posterior\n", "fix: parche posterior (T-0005)")
    second_rc, second = _release(root, no_verify=False, verify_cmd="true")
    second_versions = _version_files(root)
    second_tag_versions = _version_files_at(root, "v0.1.1")
    entries = json.loads(marker_path.read_text(encoding="utf-8"))
    changelog = (root / "CHANGELOG.md").read_text(encoding="utf-8")
    second_section = changelog.split("## v0.1.1", 1)[-1].split("## v0.1.0", 1)[0]
    empty_rc, empty_error = _release_error(root)
    no_remote_rc, no_remote_error = _release_error(root, push=True)

    project_root = Path(__file__).resolve().parents[1]
    project_versions = _version_files(project_root)
    project_tag = _latest_tag(project_root)
    import server as mcp_server

    server_version = mcp_server.SERVER_VERSION
    version_actual = {
        "dry_unchanged": dry_unchanged,
        "first": first_versions,
        "first_tag": first_tag_versions,
        "second": second_versions,
        "second_tag": second_tag_versions,
        "project": project_versions,
        "tag": project_tag,
        "server": __version__ in server_version,
    }
    version_expected = {
        "dry_unchanged": True,
        "first": {"init": "0.1.0", "pyproject": "0.1.0"},
        "first_tag": {"init": "0.1.0", "pyproject": "0.1.0"},
        "second": {"init": "0.1.1", "pyproject": "0.1.1"},
        "second_tag": {"init": "0.1.1", "pyproject": "0.1.1"},
        "project": {"init": __version__, "pyproject": __version__},
        "tag": f"v{__version__}",
        "server": True,
    }

    marker_actual = {
        "first_entries": len(first_entries),
        "first_tag": first_entries[0].get("tag") if first_entries else None,
        "first_version": first_entries[0].get("version") if first_entries else None,
        "first_sha": first_entries[0].get("released_sha") if first_entries else None,
        "first_date": bool(first_entries and first_entries[0].get("date")),
        "first_tracked": first_tracked,
        "second_rc": second_rc,
        "second_version": second.get("version"),
        "second_bump": second.get("bump"),
        "second_tickets": second.get("tickets"),
        "second_range": second.get("range"),
        "second_message_old": "T-0001" in second_section,
        "second_message_new": "T-0005" in second_section,
        "changelog_entries": changelog.count("## v"),
        "entries": len(entries),
        "tags": [entry.get("tag") for entry in entries],
        "versions": [entry.get("version") for entry in entries],
        "shas": [entry.get("released_sha") for entry in entries],
        "empty_rc": empty_rc,
        "empty_error": "No hay commits nuevos desde v0.1.1" in empty_error,
        "no_remote_rc": no_remote_rc,
        "no_remote_error": "No hay remoto configurado" in no_remote_error
        and "sin --push" in no_remote_error,
    }
    marker_expected = {
        "first_entries": 1,
        "first_tag": "v0.1.0",
        "first_version": "0.1.0",
        "first_sha": develop_head,
        "first_date": True,
        "first_tracked": ".taiki/releases.json",
        "second_rc": 0,
        "second_version": "0.1.1",
        "second_bump": "patch",
        "second_tickets": ["T-0005"],
        "second_range": develop_head,
        "second_message_old": False,
        "second_message_new": True,
        "changelog_entries": 3,
        "entries": 2,
        "tags": ["v0.1.0", "v0.1.1"],
        "versions": ["0.1.0", "0.1.1"],
        "shas": [develop_head, second.get("released_sha")],
        "empty_rc": 1,
        "empty_error": True,
        "no_remote_rc": 1,
        "no_remote_error": True,
    }
    flow_actual: dict = {}
    flow_expected: dict = {}
    if os.environ.get("TAIKI_E2E", "").strip().lower() in ("1", "true", "yes"):
        flow_actual, flow_expected = _flow(h)
    h.eq(
        {
            **negative,
            "sync": sync_actual,
            "push": push_actual,
            "bumps": bumps_actual,
            "changelog": changelog_units,
            "reconcile": reconcile_actual,
            "version": version_actual,
            "marker": marker_actual,
            "release": {
                "rc": rc,
                "develop_moved": first_develop_moved,
                "branch": release.get("branch"),
                "tag": release.get("tag"),
                "version": release.get("version"),
                "tickets": release.get("tickets"),
                "message_groups": all(
                    part in message
                    for part in ("### feat", "### fix", "T-0001", "T-0002")
                ),
                "changelog_first": all(
                    part in changelog_first
                    for part in ("## v0.1.0", "### feat", "T-0001", "T-0002")
                ),
                "changelog_second": all(
                    part in changelog
                    for part in ("## v0.1.1", "T-0005")
                ),
                "changelog_unreleased": "Unreleased" not in changelog_first,
                "changelog_version_once": changelog_first.count("## v0.1.0") == 1,
                "changelog_previous": all(
                    part in changelog_first
                    for part in ("## v0.2.0", "release anterior (T-0038)")
                ),
            },
            "flow": flow_actual,
        },
        {
            **expected_negative,
            "sync": sync_expected,
            "push": push_expected,
            "bumps": {
                "trailer_patch": {
                    "bump": "patch",
                    "explicit": [("feat: flag (T-0010)", "patch")],
                },
                "trailer_minor": {
                    "bump": "minor",
                    "explicit": [("feat: flag (T-0011)", "minor")],
                },
                "breaking_trailer_patch": {
                    "bump": "major",
                    "explicit": [("feat!: rompe api (T-0013)", "patch")],
                },
                "convention": {"bump": "minor", "explicit": []},
                "integration": {
                    "bump": "patch",
                    "explicit": [("feat: flag (T-0014)", "patch")],
                    "override": "minor",
                },
            },
            "changelog": changelog_unit_expected,
            "reconcile": reconcile_expected,
            "marker": marker_expected,
            "version": version_expected,
            "release": {
                "rc": 0,
                "develop_moved": True,
                "branch": "develop",
                "tag": "v0.1.0",
                "version": "0.1.0",
                "tickets": ["T-0001", "T-0002"],
                "message_groups": True,
                "changelog_first": True,
                "changelog_second": True,
                "changelog_unreleased": True,
                "changelog_version_once": True,
                "changelog_previous": True,
            },
            "flow": flow_expected,
        },
        "dry-run/gates rojos no escriben, el release aplana y el E2E agent→develop→main cierra el ciclo",
    )
