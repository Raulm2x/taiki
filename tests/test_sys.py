"""Workspace de sistema: helpers, ``sys status``, snapshots y contrato CLI."""

from __future__ import annotations

import argparse
import contextlib
import hashlib
import io
import json
import subprocess
from pathlib import Path

import cli
from commands.sys import (
    audit_records,
    cmd_sys_audit,
    cmd_sys_export,
    cmd_sys_snapshot,
    cmd_sys_status,
    export_records,
    snapshot_packages,
    snapshot_path,
    status,
)
from ticketlib import domain, repo, workspace
from ticketlib.domain import TicketError
from tests.seeds import git_repo
from tests.support import Harness


def run(h: Harness) -> None:
    tmp = Path(h.tmp)
    home = tmp / "home"
    config_home = home / ".config"
    git_repo(config_home)
    (config_home / "app").mkdir()
    (config_home / "app" / "conf.ini").write_text("uno\n", encoding="utf-8")
    local_bin = home / ".local" / "bin"
    local_bin.mkdir(parents=True)
    tool = local_bin / "herramienta"
    tool.write_text("#!/bin/sh\n", encoding="utf-8")

    system = git_repo(tmp / "taiki-sys")
    workspace.init_workspace(system)
    folder = system / ".taiki"
    config = workspace.load_config(folder)
    config["scope"] = {"roots": [".", str(config_home), str(local_bin)]}
    config["isolation"] = {"default": "scope", "live_branch": "live"}
    config["verify"] = {"command": "python3 tests/run.py --verify"}
    config["dotfiles"] = {"repo": str(config_home), "live": str(config_home)}
    workspace.save_config(config, folder)

    # ── Helpers de configuración ────────────────────────────────────────
    h.eq(
        (
            workspace.verify_command(config),
            workspace.verify_command({"verify": {"command": "  "}}),
        ),
        ("python3 tests/run.py --verify", None),
        "verify_command lee el comando y descarta los vacíos",
    )
    with h.env(HOME=str(home)):
        configured = workspace.dotfiles_settings(config, {})
        detected = workspace.dotfiles_settings({}, {})
    with h.env(HOME=str(tmp / "sin-dotfiles")):
        absent = workspace.dotfiles_settings({}, {})
    h.eq(
        (configured, detected, absent),
        (
            {
                "repo": str(config_home),
                "live": str(config_home),
                "detected": str(config_home),
                "source": "config",
            },
            {
                "repo": str(config_home),
                "live": str(config_home),
                "detected": str(config_home),
                "source": "git",
            },
            {
                "repo": None,
                "live": str(tmp / "sin-dotfiles" / ".config"),
                "detected": None,
                "source": None,
            },
        ),
        "dotfiles_settings prefiere config, detecta ~/.config/.git y no inventa",
    )
    h.raises(
        domain.ValidationError,
        lambda: workspace.dotfiles_settings({"dotfiles": "no"}, {}),
        "un bloque dotfiles inválido se rechaza",
    )

    # ── sys status ──────────────────────────────────────────────────────
    with h.env(HOME=str(home)):
        report = status(system)
        h.eq(
            (
                report["workspace"]["project"],
                report["git"]["root"],
                report["git"]["branch"] in ("main", "master"),
            ),
            (str(system), str(system), True),
            "sys status apunta al repo de sistema e informa de git",
        )
        h.eq(
            (
                report["scope"]["roots"],
                report["isolation"],
                report["verify"]["command"],
                report["dotfiles"]["detected"],
            ),
            (
                [str(system), str(config_home), str(local_bin)],
                {"default": "scope", "live_branch": "live"},
                "python3 tests/run.py --verify",
                str(config_home),
            ),
            "sys status expone raíces, aislamiento, verify y dotfiles",
        )

        (folder / "local.json").write_text(
            json.dumps({"scope": {"roots": [str(home / ".local" / "share")]}}),
            encoding="utf-8",
        )
        before = report["git"]["dirty"]
        (system / "suelto.txt").write_text("x", encoding="utf-8")
        after = status(system)
        h.eq(
            (after["scope"]["roots"], after["git"]["dirty"]),
            (
                [
                    str(system),
                    str(config_home),
                    str(local_bin),
                    str(home / ".local" / "share"),
                ],
                before + 1,
            ),
            "local.json suma raíces y git cuenta los cambios sin commitear",
        )

    # ── sys snapshot ────────────────────────────────────────────────────
    ticket = repo.create_ticket(h.conn, title="Ajuste del sistema", isolation="scope")
    copy = snapshot_path(
        h.conn, ticket["code"], tool, project=system, timestamp="20260101-000000"
    )
    h.eq(
        (
            copy["copied"],
            (system / copy["snapshot"]).is_file(),
            copy["sha256"],
        ),
        (True, True, hashlib.sha256(tool.read_bytes()).hexdigest()),
        "sys snapshot copia el fichero y calcula su sha256",
    )
    h.eq(
        repo.get_ticket(h.conn, ticket["code"])["reversal"],
        {
            "kind": "copy",
            "ref": copy["snapshot"],
            "command": f"cp -a {system / copy['snapshot']} {tool}",
        },
        "sys snapshot anota la reversión copy en el ticket",
    )
    h.eq(
        subprocess.run(
            ["git", "-C", str(system), "log", "-1", "--format=%s"],
            capture_output=True,
            text=True,
        ).stdout.strip(),
        f"chore(sys): snapshot {ticket['code']} {tool}",
        "sys snapshot commitea el bookkeeping con --no-verify",
    )
    fuera = tmp / "fuera.txt"
    fuera.write_text("x", encoding="utf-8")
    h.raises(
        TicketError,
        lambda: snapshot_path(h.conn, ticket["code"], fuera, project=system),
        "un fichero fuera de scope.roots se rechaza",
    )
    worktree_ticket = repo.create_ticket(h.conn, title="Árbol aislado")
    h.raises(
        TicketError,
        lambda: snapshot_path(h.conn, worktree_ticket["code"], tool, project=system),
        "sys snapshot exige aislamiento none/scope",
    )
    dot = snapshot_path(
        h.conn,
        ticket["code"],
        config_home / "app" / "conf.ini",
        project=system,
        timestamp="20260101-000001",
    )
    h.eq(
        (
            dot["copied"],
            "snapshot" in dot,
            dot["reversal"]["kind"],
            dot["hint"].startswith("taiki commit --repo"),
        ),
        (False, False, "git-revert", True),
        "dotfiles: no copia, commit cross-repo y reversión git-revert",
    )

    listing = tmp / "paquetes.txt"
    listing.write_text("bash\nvim\n", encoding="utf-8")
    packages = snapshot_packages(h.conn, ticket["code"], source=listing, project=system)
    h.eq(
        (
            (system / packages["snapshot"]).read_text(encoding="utf-8"),
            packages["packages"],
            packages["snapshot"],
        ),
        ("bash\nvim\n", 2, f"snapshots/{ticket['code']}/pacman-Qqe.txt"),
        "sys snapshot --packages guarda pacman -Qqe y reporta recuento y ruta",
    )
    # ── sys export / audit ──────────────────────────────────────────────
    export_records(h.conn, ticket["code"], project=system)
    records = system / "records"
    manifest = json.loads((records / "manifest.json").read_text(encoding="utf-8"))
    h.eq(
        (
            (records / "tickets.json").is_file(),
            (records / "tickets.md").is_file(),
            (records / "manifest.json").is_file(),
        ),
        (True, True, True),
        "sys export crea tickets.json, tickets.md y manifest.json",
    )
    h.eq(
        (
            manifest["tickets"]["count"],
            len(manifest["snapshots"]) >= 2,
            len(manifest["paths"]) == 2 + len(manifest["snapshots"]),
        ),
        (2, True, True),
        "el manifiesto cuenta tickets y registra hashes de records y snapshots",
    )
    h.eq(
        subprocess.run(
            ["git", "-C", str(system), "log", "-1", "--format=%s"],
            capture_output=True,
            text=True,
        ).stdout.strip(),
        f"chore(sys): export {ticket['code']}",
        "sys export commitea los registros sin tocar la DB",
    )
    clean = audit_records(h.conn, project=system)
    h.eq(
        (clean["changes"], clean["checked"] >= 4),
        ([], True),
        "sys audit no reporta cambios justo después del export",
    )
    changed = next(
        item["path"]
        for item in manifest["snapshots"]
        if item["path"].endswith("/herramienta")
    )
    (system / changed).write_text("#!/bin/sh\necho x\n", encoding="utf-8")
    audit = audit_records(h.conn, project=system)
    h.eq(
        [
            (item["status"], item["path"], item["covered"])
            for item in audit["changes"]
        ],
        [("changed", changed, True)],
        "sys audit detecta el hash cambiado de un snapshot de ticket abierto",
    )
    (records / "tickets.md").write_text("# otro\n", encoding="utf-8")
    uncovered = audit_records(h.conn, project=system)
    h.eq(
        [(item["status"], item["path"]) for item in uncovered["uncovered"]],
        [("changed", "records/tickets.md")],
        "sys audit marca los cambios sin ticket abierto",
    )
    h.ok(
        "no escanea ficheros nuevos" in uncovered["note"],
        "sys audit documenta que no escanea ficheros nuevos",
    )

    # ── Contrato de CLI ─────────────────────────────────────────────────
    parsed = cli.build_parser().parse_args(["sys", "status", "--json"])
    parsed_snap = cli.build_parser().parse_args(
        ["sys", "snapshot", "T-9999", "--path", "/x"]
    )
    parsed_export = cli.build_parser().parse_args(["sys", "export", "T-0001"])
    parsed_audit = cli.build_parser().parse_args(["sys", "audit"])
    h.eq(
        (
            parsed.func,
            parsed_snap.func,
            parsed_snap.path,
            parsed_export.func,
            parsed_export.code,
            parsed_audit.func,
        ),
        (
            cmd_sys_status,
            cmd_sys_snapshot,
            "/x",
            cmd_sys_export,
            "T-0001",
            cmd_sys_audit,
        ),
        "el parser enruta sys status/snapshot/export/audit",
    )
    with h.env(HOME=str(home), TAIKI_DB=str(folder / "taiki.db")):
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            code = cmd_sys_status(argparse.Namespace(json=True))
        payload = json.loads(output.getvalue())
        h.eq(
            (code, payload["workspace"]["project"]),
            (0, str(system)),
            "sys status emite JSON con el proyecto del override",
        )
