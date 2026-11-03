"""Perfil system-admin y flujo live del conductor."""

from __future__ import annotations

import re

from ticketlib import agents, runtime, workspace
from tests.support import Harness

AGENTS_DIR = runtime.ROOT / ".opencode" / "agents"
SYSTEM_ADMIN = AGENTS_DIR / "system-admin.md"
CONDUCTOR = AGENTS_DIR / "conductor.md"

INSPECT = (
    "pwd*",
    "ls*",
    "cat *",
    "head *",
    "tail *",
    "sed -n *",
    "grep *",
    "rg *",
    "find *",
    "wc *",
    "diff *",
    "file *",
    "stat *",
    "echo *",
    "printf *",
    "sha256sum *",
)
GIT = (
    "git status*",
    "git log*",
    "git diff*",
    "git show*",
    "git rev-parse*",
    "git add *",
    "git commit *",
    "git rebase*",
    "git -C *",
)
TAIKI = (
    "taiki show*",
    "taiki list*",
    "taiki status*",
    "taiki stats*",
    "taiki next*",
    "taiki worktree list*",
    "taiki commit *",
)
HIGH_RISK = (
    "sudo systemctl *",
    "sudo pacman *",
    "sudo paru *",
    "sudo cp *",
    "sudo mv *",
    "sudo install *",
    "sudo mkdir *",
    "sudo tee *",
)
DENIES = (
    "git push*",
    "git merge*",
    "taiki confirm*",
    "taiki authorize*",
    "taiki review*",
    "taiki transition*",
    "taiki complete*",
    "taiki worktree merge*",
    "rm -rf*",
    "rm -r*",
    "sudo rm*",
    "bash *",
    "sh *",
    "chmod 777*",
    "chown -R*",
)


def _split(path):
    text = path.read_text(encoding="utf-8")
    _, _, rest = text.partition("---")
    front, _, body = rest.partition("---")
    return text, front, body


def _rules(front: str) -> list[dict[str, str]]:
    rules: list[dict[str, str]] = []
    current: dict[str, str] | None = None
    for line in front.splitlines():
        stripped = line.strip()
        if stripped.startswith("- action:"):
            current = {"action": stripped.split(":", 1)[1].strip()}
            rules.append(current)
        elif current is not None and ":" in stripped:
            key, _, value = stripped.partition(":")
            current[key.strip()] = value.strip().strip('"').strip("'")
    return rules


def _rule(front: str, action: str, resource: str) -> str | None:
    effect = None
    for rule in _rules(front):
        if rule.get("action") == action and rule.get("resource") == resource:
            effect = rule.get("effect")
    return effect


def _first(front: str, action: str, resource: str) -> int:
    for index, rule in enumerate(_rules(front)):
        if rule.get("action") == action and rule.get("resource") == resource:
            return index
    return -1


def run(h: Harness) -> None:
    config = workspace.load_config(runtime.ROOT / ".taiki")
    admin = agents.registry(config)["system-admin"]
    raw = (config.get("agents") or {}).get("system-admin") or {}
    h.ok(
        (
            admin["model"],
            admin["variant"],
            admin["strengths"],
            admin["max_parallel"],
            admin["cost"],
            admin["steps"],
        )
        == ("opencode-go/deepseek-v4.1-flash", "high", [], 1, 1.5, 40)
        and (
            raw.get("model"),
            raw.get("variant"),
            raw.get("strengths"),
            raw.get("max_parallel"),
            raw.get("cost"),
            raw.get("steps"),
        )
        == ("opencode-go/deepseek-v4.1-flash", "high", [], 1, 1.5, 40)
        and agents.capability("system-admin", config)["steps"] == 40
        and all(
            agents.recommend(kind)["name"] != "system-admin"
            and agents.assign(kind)["agent"] != "system-admin"
            for kind in ("seguridad", "bug", "mantenimiento")
        ),
        "system-admin se registra igual en código y config y queda fuera del routing",
    )

    _text, front, body = _split(SYSTEM_ADMIN)
    sudo_deny = _first(front, "shell", "sudo *")
    h.ok(
        "mode: subagent" in front
        and "steps: 40" in front
        and "model: opencode-go/deepseek-v4.1-flash#high" in front
        and "description: Administra el sistema Linux con autorización previa"
        in front
        and _rule(front, "subagent", "*") == "deny"
        and _rule(front, "edit", "*") == "allow"
        and _rule(front, "shell", "*") == "deny"
        and _rule(front, "shell", "sudo *") == "deny"
        and sudo_deny >= 0
        and all(_first(front, "shell", pattern) > sudo_deny for pattern in HIGH_RISK)
        and all(_rule(front, "shell", pattern) == "deny" for pattern in DENIES)
        and _rule(front, "shell", "rm -f *") is None,
        "system-admin.md es high de 40 pasos, deniega shell por defecto y ordena sudo",
    )
    h.ok(
        all(
            _rule(front, "shell", pattern) == "allow"
            for pattern in (*INSPECT, *GIT, *TAIKI, *HIGH_RISK)
        )
        and all(
            _rule(front, "shell", pattern) == "allow"
            for pattern in (
                "python3 tests/*",
                "timeout * python3 tests/*",
                "uvx ruff *",
                "uvx mypy *",
                "systemctl --user *",
                "journalctl --user *",
            )
        ),
        "system-admin.md permite inspección, git acotado, taiki, tests, systemd y sudo de riesgo",
    )

    flat = " ".join(body.lower().split())
    _conductor_text, conductor_front, conductor_body = _split(CONDUCTOR)
    live = " ".join(conductor_body.lower().split())
    h.ok(
        all(
            token in flat
            for token in (
                "`claim`",
                "risk",
                "scope",
                "taiki authorize",
                "taiki sys snapshot",
                "taiki commit",
                "no confirmas",
                "no autorizas",
                "no revisas",
                "push",
                "merge",
            )
        )
        and re.search(
            r'- action: subagent\s*\n\s+resource: "system-admin"\s*\n\s+effect: allow',
            conductor_front,
        )
        is not None
        and all(
            token in live
            for token in (
                "isolation",
                "no se crea worktree",
                "no se lanza el merger",
                "--repo",
                "taiki authorize",
                "taiki review",
                "verify.command",
                "taiki sys snapshot",
                "taiki sys export",
            )
        ),
        "el prompt pide claim/authorize/snapshot y el conductor permite system-admin y el flujo live",
    )
