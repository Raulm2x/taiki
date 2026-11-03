"""Comandos ``taiki sys``: workspace de sistema, raíces y snapshots.

El workspace de sistema vive en un repositorio Git propio (por defecto
``~/.local/share/taiki-sys``) con su ``.taiki/`` y su DB local. Los ficheros
vivos del sistema siguen siendo la fuente de verdad; el repo de sistema actúa
como ledger de snapshots y exportaciones.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

from ticketlib import commits, render, repo, workspace
from ticketlib.domain import TicketError
from ticketlib.paths import MARKER

from .common import _conn

SNAPSHOTS_DIR = "snapshots"
RECORDS_DIR = "records"
PACKAGES_FILE = "pacman-Qqe.txt"
PACKAGES_ROLLBACK = (
    "Rollback manual: `pacman -S --needed $(cat %s)` (no se ejecuta solo)"
)
DONE_STATUSES = ("completado", "verificado", "confirmado", "cancelado")
AUDIT_NOTE = (
    "sys audit no escanea ficheros nuevos: solo verifica los paths "
    "registrados en records/manifest.json (follow-up)"
)


def project_root() -> Path:
    """Raíz del workspace de sistema, también con ``TAIKI_DB`` explícito."""
    info = workspace.resolve()
    db_file = info.get("db")
    if info.get("source") == "override" and db_file is not None:
        candidate = Path(db_file).expanduser()
        if candidate.parent.name == MARKER:
            return candidate.parent.parent
    folder = info.get("workspace")
    if folder is not None:
        return Path(folder).parent
    return workspace.project_root()


def _absolute(value: str, project: Path) -> Path:
    path = Path(value).expanduser()
    return path if path.is_absolute() else project / path


def _git_state(project: Path) -> dict[str, Any]:
    """Repo Git del workspace y cambios sin commitear (si no hay, vacío)."""
    try:
        root = commits.toplevel(project)
    except TicketError:
        return {"root": None, "branch": None, "dirty": 0, "clean": True}
    dirty = len(commits.unstaged(root))
    return {
        "root": str(root),
        "branch": commits.current_branch(root),
        "dirty": dirty,
        "clean": dirty == 0,
    }


def status(root: str | Path | None = None) -> dict[str, Any]:
    """Estado del workspace de sistema: repo, raíces, aislamiento y dotfiles."""
    project = Path(root).expanduser() if root is not None else project_root()
    folder = project / MARKER
    config = workspace.load_config(folder)
    local = workspace.load_local(folder)
    info = workspace.resolve(project)
    roots = [str(_absolute(item, project)) for item in workspace.scope_roots(config, local)]
    return {
        "workspace": {
            "project": str(project),
            "taiki": str(folder),
            "db": str(info["db"]) if info.get("db") else None,
            "source": info.get("source"),
        },
        "git": _git_state(project),
        "scope": {"roots": roots},
        "isolation": workspace.isolation_settings(config),
        "verify": {"command": workspace.verify_command(config)},
        "dotfiles": workspace.dotfiles_settings(config, local),
    }


def _resolve(path: str | Path) -> Path:
    return Path(path).expanduser().resolve()


def _stamp(timestamp: str | None = None) -> str:
    return timestamp or datetime.now().strftime("%Y%m%d-%H%M%S")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _git(root: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(root), *args],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        detail = result.stderr.strip() or f"git {' '.join(args)} falló"
        raise TicketError(detail)
    return result.stdout


def _commit_paths(project: Path, paths: list[Path], message: str) -> str:
    """Commit de bookkeeping del repo de sistema (hooks de ticket no aplican)."""
    base = _resolve(project)
    relative = [str(_resolve(path).relative_to(base)) for path in paths]
    _git(project, "add", "--", *relative)
    _git(project, "commit", "--no-verify", "-m", message)
    return _git(project, "rev-parse", "--short", "HEAD").strip()


def _snapshot_ticket(conn, code: str | None) -> dict[str, Any]:
    """Ticket válido para snapshot: existe, no archivado y en árbol vivo."""
    if not code:
        raise TicketError("Falta el código del ticket (T-XXXX)")
    ticket = repo.get_ticket(conn, code)
    if ticket.get("archived"):
        raise TicketError(f"{ticket['code']} está archivado")
    if ticket.get("isolation") not in ("none", "scope"):
        raise TicketError(
            f"{ticket['code']} usa aislamiento {ticket.get('isolation')!r}; "
            "sys snapshot solo aplica al árbol vivo (none/scope)"
        )
    return ticket


def _inside(target: Path, folder: Path) -> bool:
    try:
        target.relative_to(_resolve(folder))
    except ValueError:
        return False
    return True


def _in_roots(target: Path, project: Path, config, local) -> bool:
    return any(
        _inside(target, _absolute(value, project))
        for value in workspace.scope_roots(config, local)
    )


def _snapshot_rel(code: str, stamp: str, target: Path) -> Path:
    return Path(SNAPSHOTS_DIR, code, stamp, *target.parts[1:])


def snapshot_path(
    conn,
    code: str | None,
    target: str | Path,
    *,
    project: str | Path | None = None,
    timestamp: str | None = None,
) -> dict[str, Any]:
    """Copia un fichero vivo a ``snapshots/<code>/<timestamp>`` y lo commitea.

    Si el fichero vive en el repo de dotfiles, no se copia: el commit va allí y
    la reversión es un ``git revert`` del commit que haga el usuario.
    """
    ticket = _snapshot_ticket(conn, code)
    root = Path(project) if project is not None else project_root()
    source = _resolve(target)
    if not source.is_file():
        raise TicketError(f"No es un fichero existente: {source}")
    folder = root / MARKER
    config = workspace.load_config(folder)
    local = workspace.load_local(folder)
    if not _in_roots(source, root, config, local):
        raise TicketError(
            f"{source} queda fuera de scope.roots: "
            + ", ".join(workspace.scope_roots(config, local))
        )

    dotfiles = workspace.dotfiles_settings(config, local)
    if dotfiles["detected"] and _inside(source, Path(dotfiles["detected"])):
        repo_dir = dotfiles["detected"]
        command = f"git -C {repo_dir} revert <sha>"
        reversal = {"kind": "git-revert", "ref": repo_dir, "command": command}
        repo.update_ticket(conn, ticket["code"], reversal=reversal)
        return {
            "ticket": ticket["code"],
            "path": str(source),
            "copied": False,
            "repo": repo_dir,
            "hint": f"taiki commit --repo {repo_dir} --ticket {ticket['code']}",
            "reversal": reversal,
        }

    stamp = _stamp(timestamp)
    snapshot = _snapshot_rel(ticket["code"], stamp, source)
    destination = root / snapshot
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, destination)
    digest = _sha256(destination)
    commit = _commit_paths(
        root,
        [destination],
        f"chore(sys): snapshot {ticket['code']} {source}",
    )
    # El commit vive en el repo de sistema, no en el del ticket: se enlaza sin
    # resolver para que `complete` tenga evidencia en none/scope.
    repo.link_ticket(
        conn, ticket["code"], "commit", commit, actor="agente", resolve=False
    )
    reversal = {
        "kind": "copy",
        "ref": snapshot.as_posix(),
        "command": f"cp -a {destination} {source}",
    }
    repo.update_ticket(conn, ticket["code"], reversal=reversal)
    return {
        "ticket": ticket["code"],
        "path": str(source),
        "snapshot": snapshot.as_posix(),
        "sha256": digest,
        "commit": commit,
        "copied": True,
        "reversal": reversal,
    }


def _package_list(source: str | Path | None) -> str:
    if source is not None:
        text = Path(source).expanduser().read_text(encoding="utf-8")
    else:
        try:
            result = subprocess.run(
                ["pacman", "-Qqe"],
                capture_output=True,
                text=True,
            )
        except OSError as exc:
            raise TicketError(f"No se pudo ejecutar pacman -Qqe: {exc}") from exc
        if result.returncode != 0:
            raise TicketError(
                result.stderr.strip() or "pacman -Qqe falló; usa --from FILE"
            )
        text = result.stdout
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    return "\n".join(lines) + "\n"


def snapshot_packages(
    conn,
    code: str | None,
    *,
    source: str | Path | None = None,
    project: str | Path | None = None,
) -> dict[str, Any]:
    """Guarda la lista de paquetes instalados; el rollback es manual."""
    ticket = _snapshot_ticket(conn, code)
    root = Path(project) if project is not None else project_root()
    content = _package_list(source)
    snapshot = Path(SNAPSHOTS_DIR, ticket["code"], PACKAGES_FILE)
    destination = root / snapshot
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(content, encoding="utf-8")
    commit = _commit_paths(
        root,
        [destination],
        f"chore(sys): packages {ticket['code']}",
    )
    repo.link_ticket(
        conn, ticket["code"], "commit", commit, actor="agente", resolve=False
    )
    return {
        "ticket": ticket["code"],
        "snapshot": snapshot.as_posix(),
        "sha256": _sha256(destination),
        "commit": commit,
        "packages": len(content.splitlines()),
        "rollback": PACKAGES_ROLLBACK % (root / snapshot),
    }


def _snapshot_entries(root: Path) -> list[dict[str, str]]:
    folder = root / SNAPSHOTS_DIR
    if not folder.is_dir():
        return []
    return [
        {"path": path.relative_to(root).as_posix(), "sha256": _sha256(path)}
        for path in sorted(folder.rglob("*"))
        if path.is_file()
    ]


def _link_entries(tickets: list[dict[str, Any]]) -> dict[str, list[dict[str, str]]]:
    commits_: list[dict[str, str]] = []
    external: list[dict[str, str]] = []
    for ticket in tickets:
        for link in ticket.get("links") or []:
            item = {"ticket": ticket["code"], "ref": str(link.get("ref"))}
            if link.get("kind") == "commit":
                commits_.append(item)
            elif link.get("kind") == "external":
                external.append(item)
    return {"commits": commits_, "external": external}


def _markdown(data: dict[str, Any]) -> str:
    rows = list(data.get("tickets") or [])
    table = render.render_table(rows, use_color=False)
    return "\n".join(
        ["# Tickets Taiki", "", f"{len(rows)} tickets exportados.", "", "```", table, "```", ""]
    )


def export_records(
    conn,
    code: str | None = None,
    *,
    project: str | Path | None = None,
) -> dict[str, Any]:
    """Exporta tickets y manifiesto a ``records/`` y lo commitea.

    La DB sigue ignorada: solo se versionan los derivados y sus hashes.
    """
    root = Path(project) if project is not None else project_root()
    if code:
        code = repo.get_ticket(conn, code)["code"]
    data = repo.export_all(conn)
    records = root / RECORDS_DIR
    records.mkdir(parents=True, exist_ok=True)
    tickets_json = records / "tickets.json"
    tickets_md = records / "tickets.md"
    tickets_json.write_text(render.to_json(data) + "\n", encoding="utf-8")
    tickets_md.write_text(_markdown(data), encoding="utf-8")
    snapshots = _snapshot_entries(root)
    files = [
        {
            "path": tickets_json.relative_to(root).as_posix(),
            "sha256": _sha256(tickets_json),
        },
        {
            "path": tickets_md.relative_to(root).as_posix(),
            "sha256": _sha256(tickets_md),
        },
    ]
    manifest = {
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "tickets": {"count": len(data.get("tickets") or [])},
        "snapshots": snapshots,
        "links": _link_entries(list(data.get("tickets") or [])),
        "paths": files
        + [
            {"path": item["path"], "sha256": item["sha256"]}
            for item in snapshots
        ],
        "source": {
            "db": None,
            "note": "La DB no se versiona: solo los registros derivados.",
        },
    }
    manifest_path = records / "manifest.json"
    manifest_path.write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    message = f"chore(sys): export {code}" if code else "chore(sys): export"
    commit = _commit_paths(root, [tickets_json, tickets_md, manifest_path], message)
    if code:
        repo.link_ticket(conn, code, "commit", commit, actor="agente", resolve=False)
    return {
        "tickets": manifest["tickets"]["count"],
        "files": [item["path"] for item in files],
        "snapshots": len(snapshots),
        "manifest": manifest_path.relative_to(root).as_posix(),
        "commit": commit,
    }


def audit_records(conn, *, project: str | Path | None = None) -> dict[str, Any]:
    """Compara los hashes del manifiesto con los ficheros actuales."""
    root = Path(project) if project is not None else project_root()
    manifest_path = root / RECORDS_DIR / "manifest.json"
    if not manifest_path.is_file():
        raise TicketError(
            "No hay records/manifest.json; ejecuta `taiki sys export` primero"
        )
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise TicketError(f"Manifiesto ilegible: {exc}") from exc
    entries = list(manifest.get("paths") or [])
    open_codes = {
        row["code"]
        for row in conn.execute(
            "SELECT code FROM tickets WHERE archived = 0 "
            "AND status NOT IN (?, ?, ?, ?)",
            DONE_STATUSES,
        )
    }
    changes: list[dict[str, Any]] = []
    for item in entries:
        rel = str(item.get("path") or "")
        expected = item.get("sha256")
        current = root / rel
        if current.is_file():
            actual = _sha256(current)
            status = "changed" if actual != expected else "ok"
        else:
            actual = None
            status = "missing"
        if status == "ok":
            continue
        parts = rel.split("/")
        ticket = parts[1] if len(parts) > 2 and parts[0] == SNAPSHOTS_DIR else None
        covered = bool(ticket and ticket in open_codes)
        changes.append(
            {
                "path": rel,
                "status": status,
                "expected": expected,
                "current": actual,
                "ticket": ticket if covered else None,
                "covered": covered,
            }
        )
    return {
        "manifest": manifest_path.relative_to(root).as_posix(),
        "checked": len(entries),
        "changes": changes,
        "uncovered": [item for item in changes if not item["covered"]],
        "note": AUDIT_NOTE,
    }


def cmd_sys_export(args: argparse.Namespace) -> int:
    try:
        with _conn() as conn:
            report = export_records(conn, getattr(args, "code", None))
    except TicketError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    if getattr(args, "json", False):
        print(render.to_json(report))
        return 0
    print(f"Tickets exportados: {report['tickets']} → {', '.join(report['files'])}")
    print(f"Manifiesto: {report['manifest']} ({report['snapshots']} snapshots)")
    print(f"Commit: {report['commit']}")
    return 0


def cmd_sys_audit(args: argparse.Namespace) -> int:
    try:
        with _conn() as conn:
            report = audit_records(conn)
    except TicketError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    if getattr(args, "json", False):
        print(render.to_json(report))
        return 0
    print(f"Manifiesto: {report['manifest']} ({report['checked']} paths)")
    print(f"Cambios: {len(report['changes'])}")
    for item in report["changes"]:
        ticket = item["ticket"] or "sin ticket abierto"
        print(f"  {item['status']}: {item['path']} ({ticket})")
    print(report["note"])
    return 0


def cmd_sys_snapshot(args: argparse.Namespace) -> int:
    code = getattr(args, "code", None)
    try:
        with _conn() as conn:
            if getattr(args, "packages", False):
                report = snapshot_packages(
                    conn,
                    code,
                    source=getattr(args, "source", None),
                )
            else:
                report = snapshot_path(conn, code, args.path)
    except TicketError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    if getattr(args, "json", False):
        print(render.to_json(report))
        return 0
    if report.get("copied") is False:
        print(
            f"{report['ticket']}: {report['path']} vive en el repo de dotfiles "
            f"({report['repo']}); no se copia"
        )
        print(f"Commitea allí: {report['hint']}")
        print(f"Reversión: {report['reversal']['command']}")
        return 0
    print(
        f"{report['ticket']} snapshot: {report['snapshot']} "
        f"(sha256 {report['sha256'][:12]}…)"
    )
    print(f"Commit: {report['commit']}")
    print(f"Reversión: {report['reversal']['command']}")
    if "packages" in report:
        print(report["rollback"])
    return 0


def cmd_sys_status(args: argparse.Namespace) -> int:
    report = status(getattr(args, "root", None))
    if getattr(args, "json", False):
        print(render.to_json(report))
        return 0
    ws = report["workspace"]
    print(f"Workspace: {ws['project']} ({ws['source']})")
    print(f"Taiki: {ws['taiki']}")
    print(f"Base de datos: {ws['db'] or 'sin DB'}")
    git = report["git"]
    print(
        "Repo git: "
        f"{git['root'] or 'sin repositorio'} "
        f"({git['branch'] or 'sin rama'}, {git['dirty']} cambio(s))"
    )
    print("Raíces: " + (", ".join(report["scope"]["roots"]) or "ninguna"))
    isolation = report["isolation"]
    print(
        f"Aislamiento: {isolation['default']} · rama live {isolation['live_branch']}"
    )
    print(f"Verify: {report['verify']['command'] or 'sin comando'}")
    dotfiles = report["dotfiles"]
    if dotfiles["detected"]:
        print(f"Dotfiles: {dotfiles['detected']} ({dotfiles['source']})")
    elif dotfiles["repo"]:
        print(f"Dotfiles: {dotfiles['repo']} (sin .git)")
    else:
        print(f"Dotfiles: no detectado (live {dotfiles['live']})")
    return 0
