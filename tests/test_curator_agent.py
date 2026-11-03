"""Registro y archivos del subagente curator y su cadencia en el Conductor."""

from __future__ import annotations

import re

from ticketlib import agents, runtime, workspace
from tests.support import Harness

AGENTS_DIR = runtime.ROOT / ".opencode" / "agents"
CURATOR = AGENTS_DIR / "curator.md"
CONDUCTOR = AGENTS_DIR / "conductor.md"


def _split(path):
    text = path.read_text(encoding="utf-8")
    _, _, rest = text.partition("---")
    front, _, body = rest.partition("---")
    return text, front, body


def _denies(front: str, action: str) -> bool:
    pattern = (
        rf"- action: {re.escape(action)}\s*\n"
        r'\s+resource: "\*"\s*\n'
        r"\s+effect: deny"
    )
    return re.search(pattern, front) is not None


def run(h: Harness) -> None:
    config = workspace.load_config(runtime.ROOT / ".taiki")
    table = agents.registry(config)
    curator = table["curator"]
    h.ok("curator" in table, "el registro incluye al curator")
    h.eq(
        (
            curator["model"],
            curator["variant"],
            curator["strengths"],
            curator["max_parallel"],
            curator["cost"],
            curator["steps"],
        ),
        (
            "opencode-go/deepseek-v4.1-flash",
            "high",
            [],
            1,
            1.0,
            24,
        ),
        "el curator declara modelo, variante, límites y coste",
    )
    h.eq(
        agents.capability("curator", config)["steps"],
        24,
        "capability resuelve el curator con sus steps",
    )
    h.eq(
        agents.recommend("bug")["name"],
        "worker-high",
        "el curator no entra en el routing de tareas",
    )

    raw = (config.get("agents") or {}).get("curator") or {}
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
        "config.json declara el curator con los mismos valores",
    )
    h.eq(
        agents.registry(config)["reviewer"]["steps"],
        20,
        "la configuración conserva el resto de agentes",
    )

    _text, front, body = _split(CURATOR)
    h.ok("mode: subagent" in front, "curator.md es un subagente")
    h.ok("steps: 24" in front, "curator.md declara sus 24 pasos")
    h.ok(
        "model: opencode-go/deepseek-v4.1-flash#high" in front,
        "curator.md usa el modelo y variante acordados",
    )
    h.ok(
        _denies(front, "edit")
        and _denies(front, "shell")
        and _denies(front, "subagent"),
        "curator.md deniega edit, shell y subagentes",
    )
    lowered = " ".join((front + body).lower().split())
    h.ok(
        "memory_candidates(mode=review)" in body
        and "máximo 5" in lowered
        and "patch" in lowered
        and "supersede" in lowered
        and "evidencia" in lowered,
        "el prompt limita propuestas, prefiere patch/supersede y exige evidencia",
    )
    h.ok(
        "no apruebas" in lowered
        and "no editas código" in lowered
        and "no lanzas subagentes" in lowered,
        "el curator no aprueba, no edita y no recurre",
    )
    h.ok(
        "transitorios" in lowered
        and "secretos" in lowered
        and "prohibición amplia" in lowered,
        "el prompt fija las reglas anti-aprendizaje",
    )
    h.ok(
        "taiki://memory/candidates" in body,
        "el curator puede leer el recurso de candidatos",
    )

    _conductor_text, conductor_front, conductor_body = _split(CONDUCTOR)
    h.ok(
        re.search(
            r'- action: subagent\s*\n\s+resource: "curator"\s*\n\s+effect: allow',
            conductor_front,
        )
        is not None,
        "conductor.md permite lanzar al curator",
    )
    conductor_flat = " ".join(conductor_body.lower().split())
    h.ok(
        "cada 5" in conductor_flat
        and ">=3 candidatos" in conductor_flat
        and "background" in conductor_flat,
        "conductor.md fija la cadencia y el lanzamiento en background",
    )
    h.ok(
        "memory_candidates(mode=review)" in conductor_body
        and "no las apruebes" in conductor_flat
        and "no haya recursión" in conductor_flat,
        "el conductor reporta propuestas sin aprobarlas ni recursar",
    )
