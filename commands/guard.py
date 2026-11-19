"""Comando ``taiki guard``: ¿hay ticket activo para esta acción?"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from ticketlib import guard, workspace
from ticketlib.guard import commands as shell

# Muestras para derivar las etiquetas del guard con sus propios evaluadores:
# el resumen nunca duplica la lógica, llama a risky_git/high_risk_command.
_RISKY_SAMPLES = (
    "git add -A",
    "git add .",
    "git commit -am x",
    "git stash",
    "git reset --hard",
    "git clean -fd",
    "git checkout -- .",
    "git restore .",
)
_HIGH_RISK_SAMPLES = (
    "sudo id",
    "systemctl restart foo.service",
    "pacman -S jq",
    "rm -f /etc/x",
)
_SECRET_MARKERS = ("secret", "token", "password", "api_key", "apikey")


def _agents_dir() -> Path:
    return workspace.project_root() / ".opencode" / "agents"


def _guard_summary() -> dict[str, Any]:
    """Capa del guard reutilizando los evaluadores reales de commands.py."""
    return {
        "sin_ticket": sorted(shell._SAFE_COMMANDS),
        "git_sin_ticket": sorted(shell._SAFE_GIT),
        "git_global": sorted(
            {label for label in map(shell.risky_git, _RISKY_SAMPLES) if label}
        ),
        "alto_riesgo": sorted(
            {
                label
                for label in map(shell.high_risk_command, _HIGH_RISK_SAMPLES)
                if label
            }
        ),
    }


def _guard_reason(command: str, risky: str | None) -> dict[str, Any]:
    """Veredicto del guard para un comando con su clasificador real."""
    risk = shell.high_risk_command(command)
    if risk:
        return {
            "verdict": f"alto riesgo ({risk}): exige `taiki authorize`",
            "alternative": None,
        }
    if risky:
        return {
            "verdict": f"git global ({risky}): con otra sesión activa se bloquea",
            "alternative": shell.deny_alternative(risky),
        }
    if shell.command_is_mutating(command):
        return {
            "verdict": "escritura real: exige un ticket en_progreso",
            "alternative": None,
        }
    if shell.command_runs_code(command):
        return {
            "verdict": (
                "código pegado: en scope cuenta como escritura; usa ficheros"
            ),
            "alternative": None,
        }
    return {
        "verdict": "lectura o publicación: pasa sin ticket",
        "alternative": None,
    }


def _shell_rule_count(rules: list[dict[str, Any]]) -> int:
    """Reglas ``shell`` de un perfil: 0 significa que no permite nada."""
    return sum(1 for rule in rules if str(rule.get("action")) == "shell")


def _print_perms(
    profiles: list[dict[str, Any]], summary: dict[str, Any]
) -> None:
    if not profiles:
        print("Sin perfiles worker*.md en .opencode/agents/")
    for profile in profiles:
        print(f"Perfil {profile['name']} ({profile['origin']})")
        if not _shell_rule_count(profile["rules"]):
            print("  sin reglas shell: este perfil no permite ningún comando")
            continue
        for index, rule in enumerate(profile["rules"]):
            state = (
                "tapada"
                if not shell.rule_wins(profile["rules"], index)
                else "libre"
            )
            print(
                f"  {str(rule.get('action', '?')):8} "
                f"{str(rule.get('effect', '?')):5} "
                f"{str(rule.get('resource', '')):36} {state}"
            )
    print("  tapada = una regla posterior del mismo action solapa su patrón;")
    print("  aproximación por contención; el veredicto real va con --command")
    print("Guard de tickets (ticketlib/guard/commands.py)")
    print(f"  sin ticket (permitido): {', '.join(summary['sin_ticket'])}")
    print(f"  git sin ticket: {', '.join(summary['git_sin_ticket'])}")
    print(f"  bloquea (árbol global): {', '.join(summary['git_global'])}")
    for label in summary["git_global"]:
        alternative = shell.deny_alternative(label)
        if alternative:
            print(f"    {label}: {alternative}")
    print(f"  alto riesgo (authorize): {', '.join(summary['alto_riesgo'])}")


def cmd_perms(args: argparse.Namespace) -> int:
    """``taiki perms``: allowlist de shell, solo lectura (perfiles + guard).

    La columna ``tapada``/``libre`` aproxima por contención de patrones:
    una regla posterior del mismo action solapa el recurso literal de esta.
    El veredicto real de un comando concreto va con ``--command``.
    """
    profiles = shell.profile_rules(_agents_dir())
    if args.agent:
        wanted = (args.agent or "").strip().lower()
        profiles = [
            item
            for item in profiles
            if item["name"] == wanted or item["name"].startswith(f"{wanted}-")
        ]
    summary = _guard_summary()
    if args.command:
        rules = [rule for item in profiles for rule in item["rules"]]
        verdict = shell.command_verdict(args.command, rules)
        reason = _guard_reason(args.command, shell.risky_git(args.command))
        if args.json:
            print(
                json.dumps({**verdict, "guard": reason}, ensure_ascii=False)
            )
        else:
            for segment in verdict["segments"]:
                mark = "permitido" if segment["effect"] == "allow" else "denegado"
                rule = f" (regla {segment['rule']})" if segment["rule"] else ""
                print(f"{mark:10} {segment['segment']}{rule}")
            print(f"guard de tickets: {reason['verdict']}")
            if reason["alternative"]:
                print(f"  alternativa: {reason['alternative']}")
        return 0 if verdict["allowed"] else 3
    if args.json:
        print(
            json.dumps(
                {
                    "profiles": [
                        {
                            **item,
                            "shell_rules": _shell_rule_count(item["rules"]),
                            "rules": [
                                {
                                    **rule,
                                    "covered": not shell.rule_wins(
                                        item["rules"], index
                                    ),
                                }
                                for index, rule in enumerate(item["rules"])
                            ],
                        }
                        for item in profiles
                    ],
                    "guard": summary,
                },
                ensure_ascii=False,
            )
        )
    else:
        _print_perms(profiles, summary)
    return 0


def cmd_config_show(args: argparse.Namespace) -> int:
    """``taiki config show``: config.json resuelto, sin local.json ni secretos."""
    data = workspace.load_config()
    safe = {
        key: value
        for key, value in data.items()
        if not any(marker in key.lower() for marker in _SECRET_MARKERS)
    }
    if args.json:
        print(json.dumps(safe, ensure_ascii=False))
    else:
        print(json.dumps(safe, indent=2, ensure_ascii=False))
    return 0


def _hook() -> int:
    """Contrato genérico: JSON por stdin, decisión por stdout y exit 2 = deny."""
    try:
        payload = json.load(sys.stdin)
    except (json.JSONDecodeError, OSError) as exc:
        print(
            json.dumps(
                {"decision": "deny", "reason": f"JSON inválido: {exc}"},
                ensure_ascii=False,
            )
        )
        return 2
    data = guard.parse_hook_input(payload)
    result = guard.evaluate(
        tool=data["tool"],
        session=data["session"],
        agent=data["agent"],
        path=data["path"],
        command=data["command"],
        cwd=data.get("cwd"),
    )
    print(
        json.dumps(
            {
                "decision": "allow" if result["allow"] else "deny",
                "action": result["action"],
                "ticket": result["ticket"],
                "reason": result["message"],
            },
            ensure_ascii=False,
        )
    )
    return 0 if result["allow"] else 2


def cmd_guard(args: argparse.Namespace) -> int:
    if args.hook:
        return _hook()
    # ``_conn`` no se usa aquí: el guard abre la DB solo si hace falta.
    result = guard.evaluate(
        tool=args.tool,
        session=args.session,
        agent=args.agent,
        path=args.path,
        command=args.command,
        cwd=args.cwd,
        auto=not args.no_auto,
    )
    if args.json:
        print(json.dumps(result, ensure_ascii=False))
    elif result["message"]:
        print(result["message"], file=sys.stdout if result["allow"] else sys.stderr)
    if result["allow"]:
        return 0
    return 3 if result["ticket"] else 4
