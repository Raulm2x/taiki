"""Workspace de sistema: helpers, ``sys status`` y contrato de CLI."""

from __future__ import annotations

import argparse
import contextlib
import io
import json
from pathlib import Path

import cli
from commands.sys import cmd_sys_status, status
from ticketlib import domain, workspace
from tests.seeds import git_repo
from tests.support import Harness


def run(h: Harness) -> None:
    tmp = Path(h.tmp)
    home = tmp / "home"
    config_home = home / ".config"
    git_repo(config_home)
    (config_home / "app").mkdir()
    (config_home / "app" / "conf.ini").write_text("uno\n", encoding="utf-8")

    system = git_repo(tmp / "taiki-sys")
    workspace.init_workspace(system)
    folder = system / ".taiki"
    config = workspace.load_config(folder)
    config["scope"] = {"roots": [".", str(config_home)]}
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
        h.eq(
            workspace.dotfiles_settings(config, {}),
            {
                "repo": str(config_home),
                "live": str(config_home),
                "detected": str(config_home),
                "source": "config",
            },
            "dotfiles_settings usa el repositorio configurado",
        )
        h.eq(
            workspace.dotfiles_settings({}, {}),
            {
                "repo": str(config_home),
                "live": str(config_home),
                "detected": str(config_home),
                "source": "git",
            },
            "sin configuración detecta ~/.config/.git",
        )
    with h.env(HOME=str(tmp / "sin-dotfiles")):
        h.eq(
            (
                workspace.dotfiles_settings({}, {})["detected"],
                workspace.dotfiles_settings({}, {})["source"],
            ),
            (None, None),
            "sin configuración ni ~/.config/.git no inventa dotfiles",
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
            report["workspace"]["project"],
            str(system),
            "sys status apunta al repo de sistema",
        )
        h.eq(
            (report["git"]["root"], report["git"]["branch"] in ("main", "master")),
            (str(system), True),
            "sys status informa del repo git y de su rama",
        )
        h.eq(
            report["scope"]["roots"],
            [str(system), str(config_home)],
            "sys status resuelve las raíces relativas al proyecto",
        )
        h.eq(
            report["isolation"],
            {"default": "scope", "live_branch": "live"},
            "sys status expone el aislamiento configurado",
        )
        h.eq(
            report["verify"]["command"],
            "python3 tests/run.py --verify",
            "sys status expone verify.command",
        )
        h.eq(
            report["dotfiles"]["detected"],
            str(config_home),
            "sys status detecta los dotfiles",
        )

        (folder / "local.json").write_text(
            json.dumps({"scope": {"roots": [str(home / ".local" / "bin")]}}),
            encoding="utf-8",
        )
        h.eq(
            status(system)["scope"]["roots"],
            [str(system), str(config_home), str(home / ".local" / "bin")],
            "local.json suma raíces absolutas de la máquina",
        )

        before = report["git"]["dirty"]
        (system / "suelto.txt").write_text("x", encoding="utf-8")
        h.eq(
            status(system)["git"]["dirty"],
            before + 1,
            "sys status cuenta los cambios sin commitear",
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
