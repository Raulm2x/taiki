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
    cmd_sys_snapshot,
    cmd_sys_status,
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
        h.eq(
            status(system)["scope"]["roots"],
            [str(system), str(config_home), str(local_bin), str(home / ".local" / "share")],
            "local.json suma raíces absolutas de la máquina",
        )

        before = report["git"]["dirty"]
        (system / "suelto.txt").write_text("x", encoding="utf-8")
        h.eq(
            status(system)["git"]["dirty"],
            before + 1,
            "sys status cuenta los cambios sin commitear",
        )

    # ── sys snapshot ────────────────────────────────────────────────────
    ticket = repo.create_ticket(h.conn, title="Ajuste del sistema", isolation="scope")
    copy = snapshot_path(
        h.conn, ticket["code"], tool, project=system, timestamp="20260101-000000"
    )
    h.eq(
        (copy["copied"], (system / copy["snapshot"]).is_file()),
        (True, True),
        "sys snapshot copia el fichero al repo de sistema",
    )
    h.eq(
        copy["sha256"],
        hashlib.sha256(tool.read_bytes()).hexdigest(),
        "sys snapshot calcula el sha256 del fichero",
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
        (dot["copied"], "snapshot" in dot),
        (False, False),
        "un fichero del repo de dotfiles no se copia",
    )
    h.eq(
        (dot["reversal"]["kind"], dot["hint"].startswith("taiki commit --repo")),
        ("git-revert", True),
        "para dotfiles se indica commit cross-repo y reversión git-revert",
    )

    listing = tmp / "paquetes.txt"
    listing.write_text("bash\nvim\n", encoding="utf-8")
    packages = snapshot_packages(h.conn, ticket["code"], source=listing, project=system)
    h.eq(
        (system / packages["snapshot"]).read_text(encoding="utf-8"),
        "bash\nvim\n",
        "sys snapshot --packages guarda la lista pacman -Qqe",
    )
    h.eq(
        (packages["packages"], packages["snapshot"]),
        (2, f"snapshots/{ticket['code']}/pacman-Qqe.txt"),
        "el snapshot de paquetes informa del recuento y la ruta",
    )
    parsed_snap = cli.build_parser().parse_args(
        ["sys", "snapshot", "T-9999", "--path", "/x"]
    )
    h.eq(
        (parsed_snap.func, parsed_snap.path),
        (cmd_sys_snapshot, "/x"),
        "el parser enruta sys snapshot --path",
    )

    # ── Contrato de CLI ─────────────────────────────────────────────────
    parsed = cli.build_parser().parse_args(["sys", "status", "--json"])
    h.eq(parsed.func, cmd_sys_status, "el parser enruta sys status")
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
