"""Helpers de render compartidos por la CLI de memoria y el brief."""

from __future__ import annotations

import re
from typing import Any

CONTROL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f]")


def escape_text(value: object) -> str:
    """Convierte datos en una sola línea segura para el brief.

    La memoria es contenido no confiable: los saltos de línea y los caracteres
    de control se escapan para que una entrada nunca pueda inyectar estructura
    (cabeceras, viñetas o instrucciones) en el documento generado.
    """
    text = str(value if value is not None else "")
    text = text.replace("\\", "\\\\")
    text = text.replace("\r", "\\r").replace("\n", "\\n").replace("\t", "\\t")
    return CONTROL_RE.sub(lambda match: f"\\x{ord(match.group(0)):02x}", text)


def format_chars(value: int) -> str:
    """Miles al estilo español: 2170 -> 2.170."""
    return f"{int(value):,}".replace(",", ".")


def percentage(used: int, limit: int) -> int:
    """Porcentaje entero del presupuesto consumido."""
    if limit <= 0:
        return 0
    return int(round(100 * used / limit))


def memory_line(memory: dict[str, Any]) -> str:
    """Línea estable de una memoria para el brief (datos, no instrucciones)."""
    marker = "!" if memory.get("pinned") or memory.get("locked") else " "
    code = escape_text(memory.get("code") or "M-?")
    kind = escape_text(memory.get("kind") or "fact")
    title = escape_text(memory.get("title") or "")
    content = escape_text(memory.get("content") or "")
    head = f"- {code}{marker} [{kind}] {title}"
    return f"{head}: {content}" if content else head


def memory_summary(memory: dict[str, Any]) -> str:
    """Línea compacta para ``taiki memory list`` y ``pending``."""
    code = memory.get("code") or "M-?"
    status = memory.get("status") or "?"
    scope = memory.get("scope") or "project"
    kind = memory.get("kind") or "fact"
    title = escape_text(memory.get("title") or "")
    return f"{code}  {status:<10} {scope}/{kind:<10} {title}"


__all__ = [
    "CONTROL_RE",
    "escape_text",
    "format_chars",
    "memory_line",
    "memory_summary",
    "percentage",
]
