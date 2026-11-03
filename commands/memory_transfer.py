"""Subcomandos de export/import y rollback de memoria."""

from __future__ import annotations

import argparse

from ticketlib import render
from ticketlib.memory.export import (
    export_snapshot,
    import_snapshot,
    rollback as memory_rollback,
)

from .memory_common import _as_json, _session


def _export(conn, args: argparse.Namespace) -> int:
    result = export_snapshot(conn, args.path, args.format)
    if _as_json(args):
        print(render.to_json(result))
        return 0
    print(
        f"Exportadas {result['memories']} memoria(s) "
        f"({result['playbooks']} playbook(s)) en {result['path']}"
    )
    print(
        f"Formato {result['format']} · versión {result['version']} · "
        f"checksum {result['checksum']}"
    )
    return 0


def _import(conn, args: argparse.Namespace) -> int:
    result = import_snapshot(
        conn,
        args.path,
        strategy=args.strategy,
        dry_run=bool(args.dry_run),
    )
    if _as_json(args):
        print(render.to_json(result))
        return 0
    prefix = "Importación (dry-run)" if result["dry_run"] else "Importación"
    print(
        f"{prefix} · estrategia {result['strategy']}: "
        f"{len(result['imported'])} importada(s), "
        f"{len(result['updated'])} actualizada(s), "
        f"{len(result['skipped'])} omitida(s), "
        f"{len(result['conflicts'])} conflicto(s)"
    )
    for conflict in result["conflicts"][:10]:
        print(
            f"  {conflict['code']}: {conflict['kind']} con "
            f"{conflict['with']} → {conflict['resolution']}"
        )
    if result["archived"]:
        print("  Archivadas por rollback: " + ", ".join(result["archived"]))
    return 0


def _rollback(conn, args: argparse.Namespace) -> int:
    result = memory_rollback(
        conn,
        ledger_id=args.ledger_id,
        snapshot=args.snapshot,
        dry_run=bool(args.dry_run),
        actor="usuario",
        session_id=_session(),
        reason=args.why,
    )
    if _as_json(args):
        print(render.to_json(result))
        return 0
    suffix = " (dry-run)" if result.get("dry_run") else ""
    if result["mode"] == "ledger":
        print(
            f"Rollback del asiento {result['rolled_back']} → "
            f"asiento {result['ledger_id']}{suffix}: "
            f"{result.get('code') or 'memoria restaurada'}"
        )
    else:
        print(
            f"Rollback por snapshot {result['path']}{suffix}: "
            f"{len(result['imported'])} importada(s), "
            f"{len(result['updated'])} actualizada(s), "
            f"{len(result['archived'])} archivada(s)"
        )
    return 0
