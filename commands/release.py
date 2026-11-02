"""Comando ``taiki release``: publica la integración en ``main`` de una vez.

Solo lo ejecuta el usuario. Los perfiles de agente deniegan ``taiki *``, así
que no se añade ninguna excepción: un agente no puede publicar releases.

Flujo:

1. Comprueba que la rama de integración (``orchestrator.integration_branch``,
   ``develop`` por defecto) esté limpia y al día con su remoto, y que ``main``
   sea su ancestro. Si va por detrás, el error pide ``git pull --ff-only``; si
   va por delante, ``git push origin <rama>``; si ha divergido, resolver
   pull/rebase antes. ``--allow-ahead`` permite liberar con la rama por delante
   (y nunca por detrás): el release queda solo en local y hay que subirla
   después.
2. Ejecuta los gates (``tests/run.py --verify``). Se puede inyectar otro
   comando con ``TAIKI_RELEASE_VERIFY`` o ``--verify-cmd``; el defecto es el
   gate real.
3. Hace ``merge --squash`` de la integración sobre el ``main`` actual y crea
   **un único commit** con los tickets desde el tag anterior y bullets por tipo
   de commit. No se reescribe ``main``.
4. Alinea la versión del paquete (``ticketlib/__init__.py`` y
   ``pyproject.toml``) con ``vX.Y.Z``, actualiza ``CHANGELOG.md`` (lo crea si
   falta) y etiqueta el commit con un tag anotado ``vX.Y.Z``. Ambos ficheros de
   versión viajan en el mismo commit de release, de modo que código, commit y
   tag coinciden.
5. Reconcilia la integración con ``main``: el squash deja en ``main`` un commit
   que la rama de integración no tiene, así que se integra de vuelta con
   ``git merge --no-ff`` (idempotente: no hace nada si ``main`` ya es
   ancestro). Sin este paso la segunda release fallaría por la comprobación de
   ancestro. Al terminar se restaura el checkout original.
6. Registra la release en ``.taiki/releases.json`` (versionado): ``tag``,
   ``version``, ``released_sha`` (commit de integración aplanado) y ``date``.
   El registro viaja en el mismo merge de reconciliación o en un commit propio
   de la integración; ``main`` no cambia. La siguiente release usa ese
   ``released_sha`` como inicio del rango, así que el squash no repite commits
   ya publicados.

Con ``--push`` el comando publica en el remoto además de escribir en local, en
este orden: sube la integración si solo iba por delante, ejecuta los gates,
aplana ``main`` con su tag, publica ``main`` y el tag juntos (si el remoto
rechaza ``main`` no se reconcilia), reconcilia la integración y vuelve a subirla.
Sin ``--push`` no se toca la red: es el modo local determinista. ``--push`` y
``--dry-run`` son incompatibles.

La versión sube según los commits: ``!`` o ``BREAKING CHANGE`` → major,
``feat`` → minor y el resto → patch; ``--bump`` fuerza el incremento. Un
commit puede declarar su nivel con el trailer ``Release-Bump: patch|minor|
major`` en el cuerpo (véase ``_declared_bump``): gana el mayor declarado y un
``BREAKING CHANGE`` nunca se degrada. Si el rango no tiene commits publicables,
la release aborta con un error claro.

Rollback: ``git revert <commit>`` del commit de release (y retira el tag si aún
no se publicó). Nunca se reescribe ``main`` publicado. La reconciliación es un
merge normal: se deshace con ``git revert -m 1 <commit-de-reconciliación>`` en
la rama de integración o, mejor, revirtiendo el commit de release y volviendo a
reconciliar.
"""

from __future__ import annotations

import argparse
import json
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
RELEASES_FILE = ".taiki/releases.json"
VERSION_FILE = "ticketlib/__init__.py"
PYPROJECT_FILE = "pyproject.toml"
BUMPS = ("major", "minor", "patch")
_BUMP_RANK = {"patch": 0, "minor": 1, "major": 2}
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
# Trailer opcional en el cuerpo: deja el criterio del nivel en el commit.
_TRAILER_BUMP = re.compile(
    r"^Release-Bump:\s*(?P<level>patch|minor|major)\s*$", re.IGNORECASE | re.MULTILINE
)
# Commits que genera la propia release en la integración. No son trabajo
# publicable: si entraran en el rango, una segunda release los repetiría en el
# CHANGELOG y, sin commits nuevos, el rango nunca quedaría vacío.
_BOOKKEEPING = re.compile(
    r"^(?:"
    r"chore\(release\): v\d+\.\d+\.\d+"
    r"|chore\(release\): registrar v\d+\.\d+\.\d+"
    r"|chore: reconciliar .+ tras v\d+\.\d+\.\d+"
    r")$"
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


def _push(root: Path, remote: str, *refs: str, atomic: bool = False) -> None:
    """Empuja refs al remoto; el fallo se informa con stderr de Git."""
    args = ["git", "-C", str(root), "push"]
    if atomic:
        args.append("--atomic")
    args.extend((remote, *refs))
    result = subprocess.run(args, capture_output=True, text=True)
    if result.returncode != 0:
        detail = (result.stderr or result.stdout).strip()
        raise TicketError(detail or f"git push {remote} {' '.join(refs)} falló")



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


def _load_releases(root: Path) -> list[dict[str, Any]]:
    """Lee el marcador versionado; solo acepta una lista de entradas."""
    path = root / RELEASES_FILE
    if not path.is_file():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise TicketError(f"No se pudo leer {RELEASES_FILE}: {exc}") from exc
    if isinstance(data, dict):
        data = data.get("releases")
    if not isinstance(data, list):
        raise TicketError(f"{RELEASES_FILE} debe contener una lista de releases")
    return [entry for entry in data if isinstance(entry, dict)]


def _reconciliation_parent(root: Path, branch: str, tag: str) -> str | None:
    """Padre de integración del merge ``chore: reconciliar … tras <tag>``.

    Repos publicados antes de existir el marcador: el primer padre del merge de
    reconciliación es el commit de ``develop`` que se aplanó en ``main``.
    """
    sha = _capture(
        root,
        "log",
        "--format=%H",
        "--fixed-strings",
        "--grep",
        f"chore: reconciliar {branch} tras {tag}",
        "-n",
        "1",
        branch,
    )
    if not sha:
        return None
    return _capture(root, "rev-parse", f"{sha}^1") or None


def _range_start(
    root: Path,
    branch: str,
    main: str,
    tag: str | None,
    releases: list[dict[str, Any]],
) -> tuple[str, str]:
    """Inicio del rango y etiqueta para los mensajes de error."""
    if releases:
        last = releases[-1]
        sha = last.get("released_sha")
        if (
            isinstance(sha, str)
            and sha
            and _git_ok(root, "merge-base", "--is-ancestor", sha, branch)
        ):
            return sha, str(last.get("tag") or tag or sha)
    if tag:
        # Fallback para repos anteriores al marcador: si existe la
        # reconciliación de ese tag, su primer padre es el commit publicado.
        parent = _reconciliation_parent(root, branch, tag)
        return (parent or tag), tag
    return main, main


def _is_bookkeeping(commit: dict[str, str]) -> bool:
    return bool(_BOOKKEEPING.match(commit["subject"]))


def _write_releases(root: Path, entry: dict[str, str]) -> Path:
    """Añade o reemplaza la entrada del tag y devuelve la ruta escrita."""
    path = root / RELEASES_FILE
    entries = [item for item in _load_releases(root) if item.get("tag") != entry["tag"]]
    entries.append(entry)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(entries, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    return path


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


def _declared_bump(commit: dict[str, str]) -> str | None:
    """Nivel pedido por el trailer ``Release-Bump`` (el último si se repite)."""
    levels = _TRAILER_BUMP.findall(commit["body"])
    return levels[-1].lower() if levels else None


def _explicit_bumps(commits: list[dict[str, str]]) -> list[dict[str, str]]:
    """Commits que declaran nivel explícito, en el orden de ``git log``."""
    entries: list[dict[str, str]] = []
    for commit in commits:
        level = _declared_bump(commit)
        if level is not None:
            entries.append(
                {
                    "sha": commit["sha"],
                    "subject": commit["subject"],
                    "level": level,
                }
            )
    return entries


def _bump_for(commits: list[dict[str, str]]) -> str:
    if any(_is_breaking(commit) for commit in commits):
        return "major"
    declared = [
        level for level in (_declared_bump(commit) for commit in commits) if level
    ]
    if declared:
        return max(declared, key=_BUMP_RANK.__getitem__)
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


def _replace_line(
    text: str, key: str, version: str, section: str | None = None
) -> str:
    """Sustituye ``key = "..."`` (una vez) y conserva el resto del fichero."""
    lines = text.splitlines(keepends=True)
    current = None
    pattern = re.compile(
        rf'^(?P<head>\s*{re.escape(key)}\s*=\s*")(?P<value>[^"]*)(?P<tail>".*)$'
    )
    for index, line in enumerate(lines):
        header = line.strip()
        if header.startswith("[") and header.endswith("]"):
            current = header
            continue
        if section is not None and current != section:
            continue
        match = pattern.match(line.rstrip("\n"))
        if match:
            newline = "\n" if line.endswith("\n") else ""
            lines[index] = (
                f"{match.group('head')}{version}{match.group('tail')}{newline}"
            )
            return "".join(lines)
    where = f" en {section}" if section else ""
    raise TicketError(f"No se encontró {key} = \"...\"{where}")


def _write_version(root: Path, version: str) -> list[str]:
    """Alinea ``__version__`` y ``pyproject.toml`` con la versión del release.

    Las dos escrituras son transaccionales: primero se valida y prepara todo y
    solo después se escribe. Si algo falla, se restaura el contenido original y
    se aborta antes de crear el commit, para que el árbol no quede a medias.
    """
    targets = (
        (VERSION_FILE, "__version__", None),
        (PYPROJECT_FILE, "version", "[project]"),
    )
    originals: dict[Path, str] = {}
    planned: list[tuple[Path, str]] = []
    try:
        for relative, key, section in targets:
            path = root / relative
            if not path.is_file():
                continue
            original = path.read_text(encoding="utf-8")
            updated = _replace_line(original, key, version, section)
            originals[path] = original
            planned.append((path, updated))
        for path, updated in planned:
            path.write_text(updated, encoding="utf-8")
    except (OSError, TicketError) as exc:
        for path, original in originals.items():
            try:
                path.write_text(original, encoding="utf-8")
            except OSError:
                pass
        if isinstance(exc, TicketError):
            raise
        raise TicketError(
            f"No se pudo actualizar la versión a {version}: {exc}"
        ) from exc
    return [path.relative_to(root).as_posix() for path, _ in planned]


def _verify_command(explicit: str | list[str] | None) -> list[str]:
    if isinstance(explicit, list):
        return explicit
    raw = explicit or env.get(VERIFY_ENV)
    if raw:
        return shlex.split(raw)
    return list(DEFAULT_VERIFY)


def _reconcile(
    root: Path,
    branch: str,
    main: str,
    version: str,
    original: str,
    marker: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Integra ``main`` en ``branch`` y registra la release, sin tocar ``main``.

    Idempotente: si ``main`` ya es ancestro de la integración no hay merge,
    pero sí se escribe el marcador en un commit propio. Cuando hay
    reconciliación, el marcador se integra en el mismo merge commit
    (``merge --no-commit`` → escribir → commit), de modo que no queda un commit
    extra. Un conflicto se aborta y se informa; la release ya está publicada y
    no se revierte por ello. Restaura el checkout original pase lo que pase.
    """
    outcome: dict[str, Any] = {
        "reconciled": False,
        "reconcile_command": None,
        "release_marker": None,
    }
    needs_merge = not _git_ok(root, "merge-base", "--is-ancestor", main, branch)
    message = f"chore: reconciliar {branch} tras v{version}"
    if needs_merge:
        outcome["reconcile_command"] = (
            f"git switch {branch} && git merge --no-ff --no-verify {main} "
            f"-m {shlex.quote(message)}"
        )
    if not needs_merge and marker is None:
        return outcome
    result = subprocess.run(
        ["git", "-C", str(root), "checkout", branch],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        outcome["reconcile_error"] = (result.stderr or result.stdout).strip()
        return outcome
    failed = False
    if needs_merge:
        result = subprocess.run(
            [
                "git",
                "-C",
                str(root),
                "merge",
                "--no-ff",
                "--no-commit",
                "--no-verify",
                main,
            ],
            capture_output=True,
            text=True,
        )
        if result.returncode == 0:
            outcome["reconciled"] = True
        else:
            subprocess.run(
                ["git", "-C", str(root), "merge", "--abort"],
                capture_output=True,
                text=True,
            )
            outcome["reconcile_error"] = (result.stderr or result.stdout).strip()
            failed = True
    if marker is not None and not failed:
        path = _write_releases(root, marker)
        outcome["release_marker"] = str(path.relative_to(root))
        try:
            _git(root, "add", RELEASES_FILE)
            commit_message = (
                message if needs_merge else f"chore(release): registrar v{version}"
            )
            _git(root, "commit", "--no-verify", "-m", commit_message)
            outcome["marker_commit"] = _git(root, "rev-parse", "HEAD")
        except TicketError as exc:
            if needs_merge:
                subprocess.run(
                    ["git", "-C", str(root), "merge", "--abort"],
                    capture_output=True,
                    text=True,
                )
            outcome["reconcile_error"] = str(exc)
            failed = True
    if original != branch:
        restored = subprocess.run(
            ["git", "-C", str(root), "checkout", original],
            capture_output=True,
            text=True,
        )
        if restored.returncode != 0:
            outcome["reconcile_error"] = (
                restored.stderr or restored.stdout
            ).strip()
    return outcome


def run(
    root: str | Path | None = None,
    *,
    integration: str | None = None,
    main: str = MAIN_BRANCH,
    bump: str | None = None,
    dry_run: bool = False,
    no_verify: bool = False,
    allow_ahead: bool = False,
    push: bool = False,
    verify_cmd: str | list[str] | None = None,
) -> dict[str, Any]:
    """Publica la integración en ``main`` y devuelve el resumen del release.

    ``allow_ahead`` permite liberar cuando la integración va por delante de su
    upstream y no por detrás. La release queda solo en local: el riesgo es
    publicar un tag cuyo commit de integración no está en el remoto, así que
    hay que subir la rama a mano después. Nunca permite una rama por detrás ni
    una divergencia.

    ``push`` publica en el remoto en el mismo comando: sube la integración si
    solo iba por delante, publica ``main`` y el tag tras los gates y reconcilia
    la integración. Sin remoto o con ``--dry-run`` aborta con un error claro.
    """
    if push and dry_run:
        raise TicketError(
            "--push y --dry-run son incompatibles: el dry-run no publica nada. "
            "Quita --push para simular o --dry-run para publicar de verdad"
        )
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
    ahead = behind = 0
    remote = None
    if push:
        remotes = _capture(base, "remote").split()
        if not remotes:
            raise TicketError(
                "No hay remoto configurado; usa taiki release sin --push para "
                "publicar solo en local, o añade un remoto con "
                "git remote add origin <url>"
            )
        if upstream and "/" in upstream:
            remote = upstream.split("/", 1)[0]
        else:
            remote = "origin" if "origin" in remotes else remotes[0]
        if remote not in remotes:
            raise TicketError(
                f"El upstream de {branch} apunta al remoto {remote}, que no "
                f"existe; remotos configurados: {', '.join(remotes)}"
            )
    if upstream:
        ahead, behind = (
            int(value)
            for value in _git(base, "rev-list", "--left-right", "--count", f"{branch}...{upstream}").split()
        )
        counts = f"ahead {ahead}, behind {behind}"
        if ahead and behind:
            raise TicketError(
                f"{branch} y {upstream} han divergido ({counts}); resuelve la "
                f"divergencia antes de liberar (git pull --rebase {remote or 'origin'} "
                f"{branch} y resuelve los conflictos)"
            )
        if behind:
            raise TicketError(
                f"{branch} va {behind} commit(s) por detrás de {upstream} "
                f"({counts}); trae los cambios antes de liberar: "
                f"git switch {branch} && git pull --ff-only"
            )
        if ahead and not push and not allow_ahead:
            raise TicketError(
                f"{branch} va {ahead} commit(s) por delante de {upstream} "
                f"({counts}); sube la rama antes de liberar: "
                f"git push {remote or 'origin'} {branch}"
            )
    if not _git_ok(base, "merge-base", "--is-ancestor", main, branch):
        raise TicketError(
            f"{main} no es ancestro de {branch}; integra {main} en {branch} "
            "antes de liberar"
        )

    previous = _latest_tag(base, main)
    releases = _load_releases(base)
    start, start_label = _range_start(base, branch, main, previous, releases)
    # Los commits de housekeeping de releases anteriores (reconciliación y
    # registro del marcador) no son trabajo publicable: se excluyen del rango.
    commits = [
        commit
        for commit in _commits(base, [f"{start}..{branch}"])
        if not _is_bookkeeping(commit)
    ]
    if not commits:
        raise TicketError(f"No hay commits nuevos desde {start_label}")
    level = bump or _bump_for(commits)
    current = _parse_version(previous) if previous else (0, 0, 0)
    version = ".".join(str(part) for part in _next_version(current, level))
    tag = f"v{version}"
    if _git_ok(base, "rev-parse", "--verify", "--quiet", f"refs/tags/{tag}"):
        raise TicketError(f"El tag {tag} ya existe; usa --bump o publica una versión nueva")

    tickets = _tickets(commits)
    groups = _groups(commits)
    message = _message(version, tickets, groups)
    released_sha = _git(base, "rev-parse", branch)
    summary: dict[str, Any] = {
        "version": version,
        "tag": tag,
        "main": main,
        "integration": branch,
        "previous_tag": previous,
        "range": start,
        "released_sha": released_sha,
        "bump": level,
        "explicit_bumps": _explicit_bumps(commits),
        "tickets": tickets,
        "commits": len(commits),
        "message": message,
        "dry_run": dry_run,
        "allow_ahead": allow_ahead,
        "push": push,
        "remote": remote,
        "pushed": [],
        "reconciled": False,
        "reconcile_command": None,
    }
    if dry_run:
        return summary

    if push:
        if ahead:
            try:
                _push(base, remote, branch)
            except TicketError as exc:
                raise TicketError(
                    f"{exc}\nPaso: push de {branch}. Reintenta: "
                    f"git push {remote} {branch}"
                ) from exc
            summary["pushed"].append(branch)

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

    version_files = _write_version(base, version)
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
    if version_files:
        _git(base, "add", *version_files)
    # El commit de release agrega tickets ya verificados y no tiene un T-XXXX
    # propio; los gates se ejecutaron antes. Los hooks no aportan nada aquí.
    _git(base, "commit", "--no-verify", "-m", message)
    commit = _git(base, "rev-parse", "HEAD")
    _git(base, "tag", "-a", tag, "-m", f"Release {tag}")
    marker = {
        "tag": tag,
        "version": version,
        "released_sha": released_sha,
        "date": date.today().isoformat(),
    }
    if push:
        retry = f"git push --atomic {remote} {main} {tag}"
        try:
            _push(base, remote, main, tag, atomic=True)
        except TicketError as exc:
            if original != main:
                subprocess.run(
                    ["git", "-C", str(base), "checkout", original],
                    capture_output=True,
                    text=True,
                )
            raise TicketError(
                f"{exc}\nEl release {tag} queda solo en local ({commit[:12]}); "
                f"{main} y el tag no se publicaron y no se reconcilia "
                f"{branch}. Paso: push de {main}. Reintenta: {retry}"
            ) from exc
        summary["pushed"].extend((main, tag))
    summary.update(_reconcile(base, branch, main, version, original, marker))
    if push:
        if summary.get("reconcile_error"):
            summary["push_retry"] = f"git push {remote} {branch}"
        else:
            try:
                _push(base, remote, branch)
            except TicketError as exc:
                raise TicketError(
                    f"{exc}\n{main} y {tag} ya están publicados; falta la "
                    f"reconciliación de {branch}. Paso: push de {branch}. "
                    f"Reintenta: git push {remote} {branch}"
                ) from exc
            summary["pushed"].append(f"{branch}-reconcile")
    summary.update(
        {
            "commit": commit,
            "changelog": str(changelog_path),
            "version_files": version_files,
            "marker": RELEASES_FILE,
            "branch": _git(base, "rev-parse", "--abbrev-ref", "HEAD"),
        }
    )
    return summary


def _print_explicit(result: dict[str, Any]) -> None:
    explicit = result.get("explicit_bumps") or []
    if not explicit:
        return
    declared = ", ".join(
        f"{item['sha'][:12]} {item['subject']} → {item['level']}" for item in explicit
    )
    print(f"Nivel declarado por: {declared}")


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
            allow_ahead=bool(getattr(args, "allow_ahead", False)),
            push=bool(getattr(args, "push", False)),
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
        _print_explicit(result)
        if result.get("allow_ahead"):
            print(
                f"allow-ahead: {result['integration']} va por delante de su "
                "upstream; la release no hará push."
            )
        print("dry-run: no se ha escrito nada.")
        return 0
    print(f"Release {result['tag']} publicada en {result['main']}: {result['commit'][:12]}")
    print(f"Tag: {result['tag']}")
    if result.get("push"):
        steps = ", ".join(result["pushed"]) or "(sin pasos)"
        print(f"Publicado en {result['remote']}: {steps}")
    _print_explicit(result)
    if result.get("reconciled"):
        print(
            f"Reconciliado: {result['integration']} contiene {result['main']} "
            f"({result['reconcile_command']})"
        )
    elif result.get("reconcile_error"):
        print(
            f"Aviso: no se pudo reconciliar {result['integration']} con "
            f"{result['main']}: {result['reconcile_error']}",
            file=sys.stderr,
        )
        if result.get("push_retry"):
            print(
                f"Aviso: {result['integration']} no se ha publicado; cuando "
                f"termines la reconciliación: {result['push_retry']}",
                file=sys.stderr,
            )
    print(f"CHANGELOG: {result['changelog']}")
    print(f"Rollback: git revert {result['commit'][:12]}")
    if result.get("allow_ahead") and not result.get("push"):
        print(
            f"Aviso: release local; {result['integration']} sigue por delante "
            f"de su upstream. Súbela con git push origin {result['integration']}.",
            file=sys.stderr,
        )
    return 0
