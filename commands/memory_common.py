"""Utilidades compartidas de los comandos de memoria."""

from __future__ import annotations

import argparse
import json
import os
import re
import tempfile
from datetime import timedelta
from pathlib import Path
from typing import Any

from ticketlib import render
from ticketlib.domain import ValidationError
from ticketlib.memory import core as memory_core
from ticketlib.util import now, parse_datetime

_SINCE_RE = re.compile(r"^(\d+)d$", re.IGNORECASE)
_DIFF_FIELDS = (
    "title",
    "kind",
    "ticket_kinds",
    "key",
    "content",
    "description",
    "paths",
    "confidence",
)


def _session() -> str | None:
    return (
        os.environ.get("OPENCODE_SESSION_ID")
        or os.environ.get("AGY_SESSION_ID")
        or os.environ.get("SESSION_ID")
        or None
    )


def _as_json(args: argparse.Namespace) -> bool:
    return bool(getattr(args, "json", False))


def _parse_since(value: str | None) -> str | None:
    """Convierte ``90d`` o una fecha ISO en una marca ISO para el motor."""
    if not value:
        return None
    text = str(value).strip()
    match = _SINCE_RE.fullmatch(text)
    if match is not None:
        days = int(match.group(1))
        if days <= 0:
            raise ValidationError("--since debe ser mayor que 0 (por ejemplo 90d)")
        base = parse_datetime(now())
        if base is None:  # pragma: no cover - now() siempre es ISO válido
            return None
        return (base - timedelta(days=days)).isoformat(timespec="seconds")
    if parse_datetime(text) is None:
        raise ValidationError(
            f"--since inválido: {value!r} (usa 90d o una fecha ISO)"
        )
    return text


def _clip(value: Any, limit: int = 180) -> str:
    text = " ".join(str(value if value is not None else "").split())
    return text[:limit] + ("…" if len(text) > limit else "")


def _memory_diff(conn, memory: dict[str, Any]) -> list[dict[str, str]]:
    """Diff resumido entre una propuesta y la memoria que sustituye."""
    previous_id = memory.get("supersedes_id")
    if not previous_id:
        return []
    row = conn.execute("SELECT * FROM memories WHERE id = ?", (previous_id,)).fetchone()
    if row is None:
        return []
    previous = memory_core.row_to_memory(row)
    diffs: list[dict[str, str]] = []
    for field in _DIFF_FIELDS:
        before = previous.get(field)
        after = memory.get(field)
        if isinstance(before, list) or isinstance(after, list):
            before = ", ".join(str(item) for item in (before or []))
            after = ", ".join(str(item) for item in (after or []))
        if before == after:
            continue
        diffs.append(
            {
                "field": field,
                "before": _clip(before),
                "after": _clip(after),
            }
        )
    return diffs


def _print_diff(diffs: list[dict[str, str]]) -> None:
    if not diffs:
        print("  Sin supersesión: propuesta nueva.")
        return
    for item in diffs:
        print(f"  - {item['field']}: {item['before']}")
        print(f"  + {item['field']}: {item['after']}")


def _write_review(root: str | Path, packet: dict[str, Any]) -> Path:
    """Escribe el paquete de revisión de forma atómica."""
    folder = Path(root).expanduser() / ".taiki" / "generated"
    folder.mkdir(parents=True, exist_ok=True)
    target = folder / "memory-review.json"
    handle, temp_path = tempfile.mkstemp(
        prefix=".memory-review-", suffix=".tmp", dir=str(folder)
    )
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as stream:
            json.dump(packet, stream, ensure_ascii=False, indent=2, sort_keys=True)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp_path, target)
    except BaseException:
        try:
            os.unlink(temp_path)
        except OSError:
            pass
        raise
    return target


def _print_memory(memory: dict[str, Any], as_json: bool) -> None:
    if as_json:
        print(render.to_json(memory))
        return
    print(
        f"{memory['code']}  {memory['status']}  "
        f"{memory['scope']}/{memory['kind']}  {memory['title']}"
    )
    if memory.get("content"):
        print(f"  {memory['content']}")
    ticket_kinds = memory.get("ticket_kinds") or []
    if ticket_kinds:
        print("  Tipos de ticket: " + ", ".join(str(item) for item in ticket_kinds))
    evidence = memory.get("evidence") or []
    if evidence:
        print(
            "  Evidencia: "
            + ", ".join(f"{item['kind']}:{item['ref']}" for item in evidence)
        )
    for warning in memory.get("warnings") or []:
        print(f"  Aviso: {warning}")


def _require_user(actor: str | None) -> None:
    if str(actor or "").strip().lower() != "usuario":
        raise ValidationError(
            "Solo el usuario puede aprobar o rechazar memorias (actor 'usuario')"
        )
