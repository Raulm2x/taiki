"""Comando ``taiki release``: publica la integración en ``main`` de una vez.

Solo lo ejecuta el usuario. Los perfiles de agente deniegan ``taiki *``, así
que no se añade ninguna excepción: un agente no puede publicar releases.

Flujo:

1. Comprueba que la rama de integración (``orchestrator.integration_branch``,
   ``develop`` por defecto) esté limpia y al día con su remoto, y que ``main``
   sea su ancestro.
2. Ejecuta los gates (``tests/run.py --verify``). Se puede inyectar otro
   comando con ``TAIKI_RELEASE_VERIFY`` o ``--verify-cmd``; el defecto es el
   gate real.
3. Hace ``merge --squash`` de la integración sobre el ``main`` actual y crea
   **un único commit** con los tickets desde el tag anterior y bullets por tipo
   de commit. No se reescribe ``main``.
4. Actualiza ``CHANGELOG.md`` (lo crea si falta) y etiqueta el commit con un
   tag anotado ``vX.Y.Z``.

La versión sube según los commits: ``!`` o ``BREAKING CHANGE`` → major,
``feat`` → minor y el resto → patch; ``--bump`` fuerza el incremento.

Rollback: ``git revert <commit>`` del commit de release (y retira el tag si aún
no se publicó). Nunca se reescribe ``main`` publicado.
"""

from __future__ import annotations

import argparse
import re
import shlex
import subprocess
import sys
from datetime import date
from pathlib import Path
from typing import Any

from ticketlib import env, render, workspace
from ticketlib.domain import TicketError
from ticketlib.store.relations import TICKET_CODE_RE

MAIN_BRANCH = "main"
DEFAULT_INTEGRATION = "develop"
VERIFY_ENV = "RELEASE_VERIFY"
DEFAULT_VERIFY = (sys.executable, "tests/run.py", "--verify")
BUMPS = ("major", "minor", "patch")
# Orden estable de los grupos del resumen; el resto va al final como "otros".
TYPE_ORDER = (
    "feat",
    "fix",
    "perf",
    "refactor",
    "docs",
    "test",
    "build",
    "ci",
    "chore",
    "style",
    "revert",
)
_CONVENTIONAL = re.compile(
    r"^(?P<type>[a-zA-Z]+)(?:\([^)]*\))?(?P<breaking>!)?:\s*(?P<desc>.+)$"
)


def _git(root: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(root), *args],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        detail = result.stderr.strip() or f"git {' '.join(args)} falló"
        raise TicketError(detail)
    return result.stdout.strip()


def _git_ok(root: Path, *args: str) -> bool:
    result = subprocess.run(
        ["git", "-C", str(root), *args],
        capture_output=True,
        text=True,
    )
    return result.returncode == 0


def _capture(root: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(root), *args],
        capture_output=True,
        text=True,
    )
    return result.stdout.strip() if result.returncode == 0 else ""


def configured_integration_branch(config: dict[str, Any] | None = None) -> str:
    """Rama configurada en ``orchestrator.integration_branch``."""
    section = (config or {}).get("orchestrator")
    value = section.get("integration_branch") if isinstance(section, dict) else None
    if isinstance(value, str) and value.strip():
        return value.strip()
    return DEFAULT_INTEGRATION


def _integration_branch(root: Path, explicit: str | None) -> str:
    if explicit:
        return explicit
    config = workspace.load_config(root / ".taiki")
    return configured_integration_branch(config)


def _parse_version(text: str) -> tuple[int, int, int]:
    match = re.fullmatch(r"v?(\d+)\.(\d+)\.(\d+)", text.strip())
    if match is None:
        return (0, 0, 0)
    return tuple(int(part) for part in match.groups())  # type: ignore[return-value]


def _next_version(current: tuple[int, int, int], bump: str) -> tuple[int, int, int]:
    major, minor, patch = current
    if bump == "major":
        return (major + 1, 0, 0)
    if bump == "minor":
        return (major, minor + 1, 0)
    return (major, minor, patch + 1)


def _latest_tag(root: Path, branch: str) -> str | None:
    raw = _capture(
        root,
        "tag",
        "--list",
        "v[0-9]*",
        "--merged",
        branch,
        "--sort=-v:refname",
    )
    return raw.splitlines()[0].strip() if raw else None


def _commits(root: Path, lines: list[str]) -> list[dict[str, str]]:
    """Commits del rango con su asunto y cuerpo completo."""
    raw = _git(root, "log", "--format=%H%x1f%B%x1e", *lines)
    commits: list[dict[str, str]] = []
    for record in raw.split("\x1e"):
        record = record.strip("\n")
        if not record:
            continue
        sha, _, message = record.partition("\x1f")
        message = message.strip()
        commits.append(
            {
                "sha": sha.strip(),
                "subject": message.splitlines()[0] if message else "",
                "body": message,
            }
        )
    return commits


def _is_breaking(commit: dict[str, str]) -> bool:
    subject = commit["subject"]
    match = _CONVENTIONAL.match(subject)
    return bool((match and match.group("breaking")) or "BREAKING CHANGE" in commit["body"])


def _commit_type(commit: dict[str, str]) -> str | None:
    match = _CONVENTIONAL.match(commit["subject"])
    return match.group("type") if match else None


def _bump_for(commits: list[dict[str, str]]) -> str:
    if any(_is_breaking(commit) for commit in commits):
        return "major"
    if any(_commit_type(commit) == "feat" for commit in commits):
        return "minor"
    return "patch"


def _groups(commits: list[dict[str, str]]) -> dict[str, list[str]]:
    grouped: dict[str, list[str]] = {}
    for commit in commits:
        match = _CONVENTIONAL.match(commit["subject"])
        kind = match.group("type") if match else "otros"
        grouped.setdefault(kind, []).append(commit["subject"])
    ordered = {
        kind: grouped[kind] for kind in TYPE_ORDER if kind in grouped
    }
    ordered.update(
        (kind, subjects)
        for kind, subjects in grouped.items()
        if kind not in ordered
    )
    return ordered


def _tickets(commits: list[dict[str, str]]) -> list[str]:
    codes: list[str] = []
    # ``git log`` llega de más nuevo a más viejo; el resumen lista los
    # tickets en orden cronológico, como los introduce el trabajo.
    for commit in reversed(commits):
        for code in TICKET_CODE_RE.findall(commit["body"]):
            if code not in codes:
                codes.append(code)
    return codes


def _notes(
    tickets: list[str], groups: dict[str, list[str]]
) -> list[str]:
    lines = ["Tickets: " + (", ".join(tickets) if tickets else "(sin tickets)")]
    for kind, subjects in groups.items():
        lines.extend(["", f"### {kind}"])
        lines.extend(f"- {subject}" for subject in subjects)
    return lines


def _message(
    version: str, tickets: list[str], groups: dict[str, list[str]]
) -> str:
    lines = [f"chore(release): v{version}", ""]
    lines.extend(_notes(tickets, groups))
    return "\n".join(lines) + "\n"


def _changelog(
    existing: str, version: str, tickets: list[str], groups: dict[str, list[str]]
) -> str:
    entry = "\n".join(
        [f"## v{version} — {date.today().isoformat()}", ""]
        + _notes(tickets, groups)
    ).strip()
    if not existing.strip():
        return f"# Changelog\n\n{entry}\n"
    if existing.startswith("# "):
        title, _, rest = existing.partition("\n")
        return f"{title}\n\n{entry}\n\n{rest.lstrip(chr(10))}".rstrip() + "\n"
    return f"{entry}\n\n{existing}".rstrip() + "\n"


def _verify_command(explicit: str | list[str] | None) -> list[str]:
    if isinstance(explicit, list):
        return explicit
    raw = explicit or env.get(VERIFY_ENV)
    if raw:
        return shlex.split(raw)
    return list(DEFAULT_VERIFY)


def run(
    root: str | Path | None = None,
    *,
    integration: str | None = None,
    main: str = MAIN_BRANCH,
    bump: str | None = None,
    dry_run: bool = False,
    no_verify: bool = False,
    verify_cmd: str | list[str] | None = None,
) -> dict[str, Any]:
    """Publica la integración en ``main`` y devuelve el resumen del release."""
    if bump and bump not in BUMPS:
        raise TicketError(f"--bump debe ser uno de: {', '.join(BUMPS)}")
    base = Path(root).expanduser().resolve() if root else workspace.project_root()
    if not _git_ok(base, "rev-parse", "--git-dir"):
        raise TicketError(f"{base} no es un repositorio Git")

    branch = _integration_branch(base, integration)
    if branch == main:
        raise TicketError(
            "La rama de integración no puede ser main; configura "
            "orchestrator.integration_branch"
        )
    for name in (branch, main):
        if not _git_ok(base, "rev-parse", "--verify", f"refs/heads/{name}"):
            raise TicketError(f"No existe la rama {name}")
    if _git(base, "status", "--porcelain", "--untracked-files=no"):
        raise TicketError(
            f"Hay cambios sin commitear en {base}; deja limpio el árbol antes de liberar"
        )
    upstream = _capture(
        base, "rev-parse", "--abbrev-ref", "--symbolic-full-name", f"{branch}@{{upstream}}"
    )
    if upstream:
        ahead, behind = (
            int(value)
            for value in _git(base, "rev-list", "--left-right", "--count", f"{branch}...{upstream}").split()
        )
        if ahead or behind:
            raise TicketError(
                f"{branch} no está al día con {upstream} "
                f"(ahead {ahead}, behind {behind}); sincroniza antes de liberar"
            )
    if not _git_ok(base, "merge-base", "--is-ancestor", main, branch):
        raise TicketError(
            f"{main} no es ancestro de {branch}; integra {main} en {branch} "
            "antes de liberar"
        )

    previous = _latest_tag(base, main)
    start = previous or main
    commits = _commits(base, [f"{start}..{branch}"])
    if not commits:
        raise TicketError(f"No hay commits nuevos en {branch} desde {start}")
    level = bump or _bump_for(commits)
    current = _parse_version(previous) if previous else (0, 0, 0)
    version = ".".join(str(part) for part in _next_version(current, level))
    tag = f"v{version}"
    if _git_ok(base, "rev-parse", "--verify", "--quiet", f"refs/tags/{tag}"):
        raise TicketError(f"El tag {tag} ya existe; usa --bump o publica una versión nueva")

    tickets = _tickets(commits)
    groups = _groups(commits)
    message = _message(version, tickets, groups)
    summary: dict[str, Any] = {
        "version": version,
        "tag": tag,
        "main": main,
        "integration": branch,
        "previous_tag": previous,
        "bump": level,
        "tickets": tickets,
        "commits": len(commits),
        "message": message,
        "dry_run": dry_run,
    }
    if dry_run:
        return summary

    if not no_verify:
        command = _verify_command(verify_cmd)
        result = subprocess.run(
            command, cwd=str(base), capture_output=True, text=True
        )
        if result.returncode != 0:
            output = (result.stdout + result.stderr).strip().splitlines()
            tail = "\n".join(output[-10:])
            raise TicketError(f"Los gates fallaron; {main} no se toca:\n{tail}")

    original = _git(base, "rev-parse", "--abbrev-ref", "HEAD")
    if original != main:
        _git(base, "checkout", main)
    merged = subprocess.run(
        ["git", "-C", str(base), "merge", "--squash", branch],
        capture_output=True,
        text=True,
    )
    if merged.returncode != 0:
        subprocess.run(
            ["git", "-C", str(base), "merge", "--abort"],
            capture_output=True,
            text=True,
        )
        if original != main:
            subprocess.run(
                ["git", "-C", str(base), "checkout", original],
                capture_output=True,
                text=True,
            )
        detail = (merged.stderr or merged.stdout).strip()
        raise TicketError(f"No se pudo aplanar {branch} sobre {main}: {detail}")

    changelog_path = base / "CHANGELOG.md"
    existing = (
        changelog_path.read_text(encoding="utf-8")
        if changelog_path.is_file()
        else ""
    )
    changelog_path.write_text(
        _changelog(existing, version, tickets, groups), encoding="utf-8"
    )
    _git(base, "add", "CHANGELOG.md")
    # El commit de release agrega tickets ya verificados y no tiene un T-XXXX
    # propio; los gates se ejecutaron antes. Los hooks no aportan nada aquí.
    _git(base, "commit", "--no-verify", "-m", message)
    commit = _git(base, "rev-parse", "HEAD")
    _git(base, "tag", "-a", tag, "-m", f"Release {tag}")
    summary.update(
        {
            "commit": commit,
            "changelog": str(changelog_path),
            "branch": _git(base, "rev-parse", "--abbrev-ref", "HEAD"),
        }
    )
    return summary


def cmd_release(args: argparse.Namespace) -> int:
    root = (
        Path(args.repo).expanduser()
        if getattr(args, "repo", None)
        else workspace.project_root()
    )
    try:
        result = run(
            root,
            integration=getattr(args, "integration", None),
            bump=getattr(args, "bump", None),
            dry_run=bool(getattr(args, "dry_run", False)),
            no_verify=bool(getattr(args, "no_verify", False)),
            verify_cmd=getattr(args, "verify_cmd", None),
        )
    except TicketError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    if getattr(args, "json", False):
        print(render.to_json(result))
        return 0
    if result["dry_run"]:
        print(f"Release {result['tag']} ({result['integration']} → {result['main']})")
        print(result["message"])
        print("dry-run: no se ha escrito nada.")
        return 0
    print(f"Release {result['tag']} publicada en {result['main']}: {result['commit'][:12]}")
    print(f"Tag: {result['tag']}")
    print(f"CHANGELOG: {result['changelog']}")
    print(f"Rollback: git revert {result['commit'][:12]}")
    return 0
