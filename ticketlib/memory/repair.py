"""Reparación de evidencia con el tipo embebido en la referencia.

Algunas filas de ``memory_evidence`` guardan el tipo dentro de ``ref``
(``file:/ruta``, ``ticket:T-0012``, ``commit:880faa1``, ``session:ses_…``).
``evidence_freshness`` las interpreta como ficheros y las marca no vigentes;
este módulo separa el prefijo en ``kind`` y ``ref`` sin tocar el código ni el
contenido de la memoria. Las referencias sin prefijo conocido no se tocan.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

from .. import db, workspace
from ..domain import ValidationError
from ..gitutil import resolve_commit
from ..util import now
from . import ledger as ledger_lib

PREFIXES = ("file", "ticket", "commit", "url", "session")
REPAIR_REASON = "prefijo embebido normalizado"
_UNCHECKED = ("url", "session")


def _split_ref(ref: str) -> tuple[str, str] | None:
    """Separa ``<prefijo>:<referencia>`` o devuelve ``None`` si no aplica."""
    for prefix in PREFIXES:
        marker = prefix + ":"
        if ref.startswith(marker):
            return prefix, ref[len(marker):].strip()
    return None


def _base_root(root: str | Path | None) -> Path:
    if root is not None:
        return Path(root).expanduser()
    return Path(workspace.checkout_root()).expanduser()


def _commit_roots(root: Path) -> list[str]:
    """Repos donde buscar un commit: la raíz y el repo de dotfiles."""
    roots = [str(root)]
    try:
        settings = workspace.dotfiles_settings()
    except ValidationError:
        settings = {}
    if isinstance(settings, dict):
        for key in ("detected", "repo"):
            value = settings.get(key)
            if value and str(value) not in roots:
                roots.append(str(value))
    return roots


def _reason(
    conn: sqlite3.Connection,
    kind: str,
    ref: str,
    root: Path,
    commit_roots: list[str],
) -> str | None:
    """Motivo por el que la referencia no vale, o ``None`` si es válida."""
    if not ref:
        return "referencia vacía"
    if kind in _UNCHECKED:
        return None
    if kind == "file":
        path = Path(ref).expanduser()
        if not path.is_absolute():
            path = root / path
        return None if path.exists() else f"fichero {ref} inexistente"
    if kind == "ticket":
        row = conn.execute(
            "SELECT 1 FROM tickets WHERE code = ?", (ref,)
        ).fetchone()
        return None if row is not None else f"ticket {ref} inexistente"
    if any(resolve_commit(ref, repo) is not None for repo in commit_roots):
        return None
    return f"commit {ref} inexistente"


def plan_repair(
    conn: sqlite3.Connection,
    root: str | Path | None = None,
) -> dict[str, Any]:
    """Plan de normalización: no escribe nada.

    Devuelve ``changes`` (una entrada por fila con prefijo conocido, con
    ``valid``/``reason``), los conteos ``valid``/``invalid`` y el número de
    ``memories`` afectadas por cambios válidos. Las filas sin prefijo se omiten.
    """
    base = _base_root(root)
    commit_roots = _commit_roots(base)
    rows = conn.execute(
        "SELECT e.memory_id, m.code, e.kind, e.ref FROM memory_evidence e "
        "JOIN memories m ON m.id = e.memory_id "
        "ORDER BY e.memory_id, e.kind, e.ref"
    ).fetchall()
    changes: list[dict[str, Any]] = []
    memories: set[int] = set()
    invalid = 0
    for row in rows:
        ref = str(row["ref"] or "")
        split = _split_ref(ref)
        if split is None:
            continue
        new_kind, new_ref = split
        reason = _reason(conn, new_kind, new_ref, base, commit_roots)
        memory_id = int(row["memory_id"])
        if reason is None:
            memories.add(memory_id)
        else:
            invalid += 1
        changes.append(
            {
                "memory_id": memory_id,
                "code": str(row["code"]),
                "kind": str(row["kind"] or ""),
                "ref": ref,
                "new_kind": new_kind,
                "new_ref": new_ref,
                "valid": reason is None,
                "reason": reason,
            }
        )
    return {
        "changes": changes,
        "valid": len(changes) - invalid,
        "invalid": invalid,
        "memories": len(memories),
        "root": str(base),
    }


def apply_repair(
    conn: sqlite3.Connection,
    root: str | Path | None = None,
    actor: str = "agente",
) -> dict[str, Any]:
    """Normaliza todas las referencias con prefijo y deja un asiento por memoria.

    Es idempotente: tras aplicarlo, ``plan_repair`` ya no encuentra prefijos.
    Las referencias inválidas también se normalizan; ``freshness`` las marcará
    ``stale`` con su motivo real (fichero, ticket o commit inexistente).
    """
    plan = plan_repair(conn, root)
    changes: list[dict[str, Any]] = plan["changes"]
    ledger_ids: list[int] = []
    if changes:
        by_memory: dict[int, list[dict[str, Any]]] = {}
        for item in changes:
            by_memory.setdefault(int(item["memory_id"]), []).append(item)
        with db.tx(conn):
            for memory_id, items in sorted(by_memory.items()):
                before = ledger_lib.snapshot(conn, memory_id)
                for item in items:
                    conn.execute(
                        "DELETE FROM memory_evidence "
                        "WHERE memory_id = ? AND kind = ? AND ref = ?",
                        (memory_id, item["kind"], item["ref"]),
                    )
                    conn.execute(
                        "INSERT OR IGNORE INTO memory_evidence("
                        "memory_id, kind, ref, created_at) VALUES (?, ?, ?, ?)",
                        (memory_id, item["new_kind"], item["new_ref"], now()),
                    )
                after = ledger_lib.snapshot(conn, memory_id)
                ledger_ids.append(
                    ledger_lib.record(
                        conn,
                        action="repair-evidence",
                        memory_id=memory_id,
                        before=before,
                        after=after,
                        actor=actor,
                        reason=REPAIR_REASON,
                        meta={
                            "invalid": sum(1 for item in items if not item["valid"])
                        },
                    )
                )
    return {
        **plan,
        "applied": len(changes),
        "ledger": ledger_ids,
    }


__all__ = ["PREFIXES", "apply_repair", "plan_repair"]
