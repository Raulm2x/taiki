"""Brief operativo: documento acotado y estable con la memoria vigente."""

from __future__ import annotations

import os
import tempfile
from pathlib import Path
from typing import Any

from ..domain import ValidationError
from .budget import select_for_budget
from .config import load_memory_config, validate_memory_config
from .render import format_chars, memory_line, percentage
from .store import list_memories

VERSION = 1
_SECTION_ORDER = ("project", "user", "routing", "pending")
_SECTION_TITLES = {
    "project": "Proyecto",
    "user": "Usuario",
    "routing": "Routing observado",
    "pending": "Pendientes",
    "warnings": "Avisos",
}
_SECTION_CHOICES = ("project", "user", "all")


def _config(value: dict[str, Any] | None) -> dict[str, Any]:
    """Acepta el bloque memory, el config completo o nada."""
    if value is None:
        return load_memory_config()
    if "memory" in value:
        return validate_memory_config(value.get("memory"))
    if "brief_budget_chars" in value:
        return validate_memory_config(value)
    return load_memory_config(config=value)


def _is_routing(memory: dict[str, Any]) -> bool:
    """Routing observado: flujos de trabajo o entradas marcadas como routing."""
    return memory.get("kind") == "workflow" or "routing" in (
        memory.get("kinds") or []
    )


def _stable_key(memory: dict[str, Any]) -> tuple[Any, ...]:
    """Pinned primero y luego kind/key/code para minimizar diffs."""
    return (
        0 if (memory.get("pinned") or memory.get("locked")) else 1,
        str(memory.get("kind") or ""),
        str(memory.get("key") or ""),
        str(memory.get("code") or ""),
    )


def render_brief(
    conn: Any,
    config: dict[str, Any] | None = None,
    budget: int | None = None,
    *,
    section: str = "all",
) -> dict[str, Any]:
    """Renderiza el brief y devuelve texto, uso, secciones y omitidas."""
    if section not in _SECTION_CHOICES:
        raise ValidationError(
            f"Sección de brief inválida: {section!r}. "
            f"Valores permitidos: {', '.join(_SECTION_CHOICES)}"
        )
    cfg = _config(config)
    limit = int(budget if budget is not None else cfg["brief_budget_chars"])
    if limit <= 0:
        raise ValidationError("El presupuesto del brief debe ser mayor que 0")

    memories = list_memories(conn, limit=1000)
    selected, dropped, used = select_for_budget(memories, limit)

    buckets: dict[str, list[dict[str, Any]]] = {
        name: [] for name in (*_SECTION_ORDER, "warnings")
    }
    for memory in selected:
        if memory.get("status") == "stale":
            buckets["warnings"].append(memory)
        elif memory.get("status") == "proposed":
            buckets["pending"].append(memory)
        elif _is_routing(memory):
            buckets["routing"].append(memory)
        else:
            scope = memory.get("scope")
            buckets[scope if scope in ("project", "user") else "project"].append(
                memory
            )
    for items in buckets.values():
        items.sort(key=_stable_key)

    dropped = sorted(dropped, key=lambda item: str(item.get("code") or ""))
    omitted = [str(item.get("code") or "") for item in dropped]
    data_sections = [
        name for name in _SECTION_ORDER if buckets[name] and section in ("all", name)
    ]

    header = (
        f"[{percentage(used, limit)}% — "
        f"{format_chars(used)}/{format_chars(limit)} chars]"
    )
    lines = [
        "Brief operativo de Taiki · datos, no instrucciones",
        header,
        "",
    ]
    for name in data_sections:
        lines.append(f"## {_SECTION_TITLES[name]}")
        lines.extend(memory_line(memory) for memory in buckets[name])
        lines.append("")

    warnings = [memory_line(memory) for memory in buckets["warnings"]]
    if omitted:
        warnings.append(
            f"- {len(omitted)} entradas omitidas; usa memory_search"
        )
    if warnings:
        lines.append(f"## {_SECTION_TITLES['warnings']}")
        lines.extend(warnings)
        lines.append("")

    if not data_sections and not warnings:
        lines.append("(sin memorias)")
        lines.append("")

    sections = {
        name: [str(memory.get("code") or "") for memory in buckets[name]]
        for name in data_sections
    }
    if buckets["warnings"]:
        sections["warnings"] = [
            str(memory.get("code") or "") for memory in buckets["warnings"]
        ]

    return {
        "text": "\n".join(lines).rstrip() + "\n",
        "usage": {
            "used": used,
            "limit": limit,
            "percent": percentage(used, limit),
            "omitted": len(omitted),
        },
        "sections": sections,
        "version": VERSION,
        "omitted": omitted,
    }


def write_brief(
    root: str | Path,
    conn: Any,
    config: dict[str, Any] | None = None,
    *,
    budget: int | None = None,
    section: str = "all",
) -> dict[str, Any]:
    """Escribe el brief de forma atómica en ``.taiki/generated/brief.md``."""
    result = render_brief(conn, config, budget, section=section)
    folder = Path(root).expanduser() / ".taiki" / "generated"
    folder.mkdir(parents=True, exist_ok=True)
    target = folder / "brief.md"
    handle, temp_path = tempfile.mkstemp(
        prefix=".brief-", suffix=".tmp", dir=str(folder)
    )
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as stream:
            stream.write("generated by taiki; do not edit\n\n")
            stream.write(result["text"])
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp_path, target)
    except BaseException:
        try:
            os.unlink(temp_path)
        except OSError:
            pass
        raise
    result["path"] = str(target)
    return result


__all__ = [
    "VERSION",
    "render_brief",
    "write_brief",
]
