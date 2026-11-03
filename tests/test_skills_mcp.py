"""Contrato MCP de skills: skills_suggest de solo lectura, sin escritura."""

from __future__ import annotations

import hashlib
import json

from mcp_layer import handlers, schemas
from mcp_layer import skills as mcp_skills
from ticketlib import domain
from ticketlib.skills.registry import bundle_hash
from tests.support import Harness

NAMES = ("python-testing-patterns", "sqlalchemy", "pandas-pro")


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _entry(name: str) -> dict:
    hashes = {"SKILL.md": _sha(f"# {name}\n")}
    return {
        "source": "midudev/autoskills",
        "skillPath": f"midudev/autoskills/{name}",
        "commitSha": "0123456789abcdef0123456789abcdef01234567",
        "files": ["SKILL.md"],
        "sha256": hashes,
        "bundleHash": bundle_hash(hashes),
        "review": {"status": "approved", "flags": [], "summary": "ok"},
        "securityCheck": {"status": "ok", "findings": [], "summary": "ok"},
    }


def _registry() -> dict:
    return {"version": 1, "skills": {name: _entry(name) for name in NAMES}}


def _tool(name: str) -> dict:
    return next(tool for tool in schemas.TOOLS if tool["name"] == name)


def _raises(func) -> bool:
    try:
        func()
    except domain.ValidationError:
        return True
    return False


def run(h: Harness) -> None:
    # ── Catálogo: una tool de lectura y ninguna de escritura ─────────────
    suggest_tool = _tool("skills_suggest")
    names = {tool["name"] for tool in schemas.TOOLS}
    h.ok(
        suggest_tool["annotations"] == {"readOnlyHint": True},
        "skills_suggest se anota como readOnlyHint",
    )
    h.ok(
        "skills_suggest" not in handlers.MUTATING_TOOLS,
        "skills_suggest no es una herramienta mutante",
    )
    h.eq(
        handlers.TOOL_DISPATCH.get("skills_suggest"),
        mcp_skills.handle_skills_suggest,
        "TOOL_DISPATCH apunta al handler de skills",
    )
    h.ok(
        {"skills_install", "skills_remove", "skills_sync"}.isdisjoint(names),
        "el catálogo MCP no expone install, remove ni sync de skills",
    )
    h.ok(
        all(
            not name.startswith("skills_") or name == "skills_suggest"
            for name in handlers.TOOL_DISPATCH
        ),
        "la única tool MCP de skills es de solo lectura",
    )
    h.eq(
        set(suggest_tool["inputSchema"]["properties"]),
        {"techs", "limit"},
        "skills_suggest solo acepta techs y limit",
    )
    h.eq(
        suggest_tool["inputSchema"]["required"],
        [],
        "skills_suggest no exige argumentos",
    )

    # ── Envelope con techs explícitas ────────────────────────────────────
    registry_path = h.tmp / "index.json"
    registry_path.write_text(json.dumps(_registry()), encoding="utf-8")
    with h.env(TAIKI_SKILLS_REGISTRY=str(registry_path)):
        result = handlers.call_tool(
            "skills_suggest",
            {"techs": ["pytest", "python", "sqlalchemy"], "limit": 2},
        )
        h.eq(
            result["techs"],
            ["pytest", "python", "sqlalchemy"],
            "skills_suggest respeta las techs recibidas",
        )
        h.eq(result["count"], 2, "skills_suggest aplica el límite")
        h.eq(
            [item["name"] for item in result["suggestions"]],
            ["python-testing-patterns", "sqlalchemy"],
            "skills_suggest devuelve las skills del registro en orden",
        )
        first = result["suggestions"][0]
        h.ok(
            first["source"] == "midudev/autoskills"
            and first["hash"] == _entry("python-testing-patterns")["bundleHash"]
            and first["review"]["status"] == "approved"
            and first["securityCheck"]["status"] == "ok",
            "el contrato MCP incluye source/hash/review/securityCheck",
        )
        h.ok(
            result["root"]
            and isinstance(result["root"], str)
            and set(result["suggestions"][0]["techs"]) == {"python", "pytest"},
            "el envelope incluye root y las techs agregadas",
        )

        # ── Detección por defecto sin techs ──────────────────────────────
        original_detect = mcp_skills.detect_techs
        mcp_skills.detect_techs = lambda root: ["pytest"]
        try:
            detected = handlers.call_tool("skills_suggest", {})
        finally:
            mcp_skills.detect_techs = original_detect
        h.eq(
            detected["techs"],
            ["pytest"],
            "sin techs se usa detect_techs",
        )
        h.eq(
            [item["name"] for item in detected["suggestions"]],
            ["python-testing-patterns"],
            "la detección por defecto alimenta las sugerencias",
        )

        h.ok(
            _raises(
                lambda: handlers.call_tool(
                    "skills_suggest", {"techs": "pytest"}
                )
            ),
            "techs debe ser una lista",
        )
        h.ok(
            _raises(
                lambda: handlers.call_tool(
                    "skills_suggest", {"techs": ["pytest"], "limit": 0}
                )
            )
            and _raises(
                lambda: handlers.call_tool(
                    "skills_suggest", {"techs": ["pytest"], "limit": 101}
                )
            ),
            "limit fuera de rango se rechaza",
        )
        h.ok(
            _raises(
                lambda: handlers.call_tool(
                    "skills_suggest", {"techs": ["pytest"], "install": True}
                )
            ),
            "skills_suggest rechaza argumentos desconocidos",
        )
        h.ok(
            _raises(
                lambda: handlers.call_tool(
                    "skills_suggest", {"techs": ["x" * 81]}
                )
            ),
            "una tecnología demasiado larga se rechaza",
        )

    # Un registro local inexistente falla sin tocar la red.
    with h.env(TAIKI_SKILLS_REGISTRY=str(h.tmp / "no-existe.json")):
        h.ok(
            _raises(
                lambda: handlers.call_tool(
                    "skills_suggest", {"techs": ["pytest"]}
                )
            ),
            "un registro inaccesible se reporta como ValidationError",
        )
