"""Registro y archivos del subagente merger y su delegación desde el Conductor."""

from __future__ import annotations

import re

from ticketlib import agents, runtime, workspace
from tests.support import Harness

AGENTS_DIR = runtime.ROOT / ".opencode" / "agents"
MERGER = AGENTS_DIR / "merger.md"
CONDUCTOR = AGENTS_DIR / "conductor.md"


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


def _effect(front: str, action: str, resource: str) -> str | None:
    effect = None
    for rule in _rules(front):
        if rule.get("action") == action and rule.get("resource") == resource:
            effect = rule.get("effect")
    return effect


def _denies(front: str, action: str) -> bool:
    return _effect(front, action, "*") == "deny"


def run(h: Harness) -> None:
    config = workspace.load_config(runtime.ROOT / ".taiki")
    table = agents.registry(config)
    merger = table["merger"]
    h.ok("merger" in table, "el registro incluye al merger")
    h.eq(
        (
            merger["model"],
            merger["variant"],
            merger["strengths"],
            merger["max_parallel"],
            merger["cost"],
            merger["steps"],
        ),
        (
            "opencode-go/deepseek-v4.1-flash",
            "high",
            [],
            1,
            1.0,
            24,
        ),
        "el merger declara modelo, variante, límites y coste",
    )
    h.eq(
        agents.capability("merger", config)["steps"],
        24,
        "capability resuelve el merger con sus steps",
    )
    h.eq(
        agents.recommend("bug")["name"],
        "worker-high",
        "el merger no entra en el routing de tareas",
    )

    raw = (config.get("agents") or {}).get("merger") or {}
    h.eq(
        (
            raw.get("model"),
            raw.get("variant"),
            raw.get("strengths"),
            raw.get("max_parallel"),
            raw.get("cost"),
            raw.get("steps"),
        ),
        (
            "opencode-go/deepseek-v4.1-flash",
            "high",
            [],
            1,
            1.0,
            24,
        ),
        "config.json declara el merger con los mismos valores",
    )
    h.eq(
        agents.registry(config)["reviewer"]["steps"],
        20,
        "config.json mantiene el resto de perfiles intactos",
    )

    _text, front, body = _split(MERGER)
    h.ok("mode: subagent" in front, "merger.md es un subagente")
    h.ok("steps: 24" in front, "merger.md declara sus 24 pasos")
    h.ok(
        "model: opencode-go/deepseek-v4.1-flash#high" in front,
        "merger.md usa el modelo y variante acordados",
    )
    h.ok(
        _denies(front, "edit")
        and _denies(front, "subagent")
        and _denies(front, "shell"),
        "merger.md deniega edit, shell y subagentes",
    )
    h.ok(
        _effect(front, "shell", "taiki worktree merge*") == "allow"
        and _effect(front, "shell", "taiki worktree list*") == "allow"
        and _effect(front, "shell", "python3 tests/*") == "allow"
        and _effect(front, "shell", "timeout * python3 tests/*") == "allow",
        "el merger integra worktrees y puede ejecutar los tests",
    )
    h.ok(
        _effect(front, "shell", "taiki worktree merge*--force*") == "deny"
        and _effect(front, "shell", "taiki worktree merge*--no-tests*") == "deny"
        and _effect(front, "shell", "taiki worktree merge*--path*") == "deny"
        and _effect(front, "shell", "taiki worktree remove*") == "deny",
        "el merger no fuerza, no salta tests, no cambia el path ni retira worktrees",
    )
    h.ok(
        all(
            _effect(front, "shell", pattern) == "deny"
            for pattern in (
                "taiki confirm*",
                "taiki transition*",
                "taiki commit*",
                "taiki claim*",
                "taiki complete*",
                "git push*",
                "git merge*",
                "git rebase*",
                "git add*",
            )
        ),
        "el merger no confirma, no escribe estado, no commitea ni publica",
    )
    h.ok(
        all(
            _effect(front, "shell", pattern) == "deny"
            for pattern in (
                "rm -rf*",
                "rm -fr*",
                "rm -r*",
                "rm *",
                "git rm *",
                "sudo *",
                "bash *",
                "sh *",
                "uvx *",
            )
        )
        and _effect(front, "shell", "rm -f *") is None,
        "el merger no borra, no usa sudo/bash ni uvx",
    )
    lowered = " ".join((front + body).lower().split())
    h.ok(
        "lista ordenada" in lowered
        and "primer fallo" in lowered
        and "informe final" in lowered
        and "confirmado" in lowered,
        "el prompt exige lista ordenada, parada en el primer fallo e informe",
    )
    h.ok(
        "no editas" in lowered
        and "no confirmas" in lowered
        and "--force" in lowered
        and "--no-tests" in lowered
        and "--path" in lowered,
        "el prompt prohíbe editar, confirmar y usar flags de bypass",
    )

    _conductor_text, conductor_front, conductor_body = _split(CONDUCTOR)
    h.ok(
        re.search(
            r'- action: subagent\s*\n\s+resource: "merger"\s*\n\s+effect: allow',
            conductor_front,
        )
        is not None,
        "conductor.md permite lanzar al merger",
    )
    conductor_flat = " ".join(conductor_body.lower().split())
    h.ok(
        "merger" in conductor_flat
        and "lista ordenada" in conductor_flat
        and "fallback" in conductor_flat
        and "nunca ejecutes `taiki confirm`" in conductor_flat,
        "conductor.md delega la integración y conserva el merge como fallback",
    )
