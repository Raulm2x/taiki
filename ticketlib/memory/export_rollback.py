"""Rollback de la memoria por asiento de ledger o por snapshot."""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

from ..domain import ValidationError
from . import ledger as ledger_lib
from .export_import import _import


def rollback(
    conn: sqlite3.Connection,
    *,
    ledger_id: int | None = None,
    snapshot: str | Path | None = None,
    dry_run: bool = False,
    actor: str | None = None,
    session_id: str | None = None,
    reason: str | None = None,
) -> dict[str, Any]:
    """Revierte por asiento de ledger o por snapshot, con dry-run opcional."""
    if (ledger_id is None) == (snapshot is None):
        raise ValidationError(
            "Indica un ledger-id o --snapshot, exactamente uno"
        )
    if snapshot is not None:
        result = _import(
            conn,
            snapshot,
            strategy="replace",
            dry_run=dry_run,
            action="rollback",
            exact=True,
            actor=actor,
            session_id=session_id,
            reason=reason,
        )
        result["mode"] = "snapshot"
        return result
    assert ledger_id is not None
    if dry_run:
        conn.execute("SAVEPOINT memory_rollback_dry_run")
    try:
        result = ledger_lib.rollback(
            conn,
            int(ledger_id),
            actor=actor,
            session_id=session_id,
            reason=reason or f"rollback del asiento {ledger_id}",
        )
    finally:
        if dry_run:
            conn.execute("ROLLBACK TO memory_rollback_dry_run")
            conn.execute("RELEASE memory_rollback_dry_run")
    result["mode"] = "ledger"
    result["dry_run"] = bool(dry_run)
    return result
