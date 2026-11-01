"""Comando ``ticket servers``: inventario y limpieza de servidores MCP."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from ticketlib import db, servers, workspace


def _fmt(value: object, width: int) -> str:
    return str(value if value is not None else "-")[:width]


def _space(value: object) -> str:
    if not value:
        return "-"
    path = Path(str(value))
    return path.parent.name if path.name == ".taiki" else path.name


def cmd_servers(args: argparse.Namespace) -> int:
    if args.apply and not args.prune:
        print("Error: --apply requiere --prune", file=sys.stderr)
        return 2
    if args.duplicates and not args.prune:
        print("Error: --duplicates requiere --prune", file=sys.stderr)
        return 2
    conn = db.connect(workspace.registry_path())
    try:
        entries = servers.inventory(conn)
        plan = (
            servers.prune(
                conn, apply=args.apply, duplicates=args.duplicates
            )
            if args.prune
            else None
        )
    finally:
        conn.close()

    if args.json:
        print(json.dumps({"servers": entries, "prune": plan}, ensure_ascii=False))
        return 0

    if not entries:
        print("No hay servidores MCP de tickets en ejecución.")
    else:
        print(
            f"{'PID':>7}  {'PADRE':>7}  {'ESTADO':<15} {'ESPACIO':<18} "
            f"{'HUELLA':<13} {'ARRANQUE':<19} LATIDO"
        )
        for item in entries:
            print(
                f"{item['pid']:>7}  {str(item['ppid'] or '-'):>7}  "
                f"{item['state']:<15} {_space(item.get('workspace')):<18} "
                f"{_fmt(item['fingerprint'], 13):<13} "
                f"{_fmt(item['started_at'], 19):<19} {_fmt(item['heartbeat_at'], 19)}"
            )
        counts: dict[str, int] = {}
        for item in entries:
            counts[item["state"]] = counts.get(item["state"], 0) + 1
        summary = " · ".join(f"{name}: {count}" for name, count in counts.items())
        print(f"\n{len(entries)} servidor(es) · {summary}")

    if plan is not None:
        if not args.apply:
            targets = plan["orphans"] + plan["duplicates"] + plan["dead"]
            if targets:
                print(
                    "Se limpiarían: "
                    + ", ".join(str(pid) for pid in targets)
                    + " (simulación; usa --apply)"
                )
            else:
                print("Nada que limpiar.")
        else:
            if plan["killed"]:
                print("Terminados: " + ", ".join(str(pid) for pid in plan["killed"]))
            if plan["dead"]:
                print(
                    "Registros eliminados: "
                    + ", ".join(str(pid) for pid in plan["dead"])
                )
            if plan["duplicates"]:
                print(
                    "Duplicados terminados: "
                    + ", ".join(str(pid) for pid in plan["duplicates"])
                )
            if plan["stubborn"]:
                print(
                    "No se pudo terminar: "
                    + ", ".join(str(pid) for pid in plan["stubborn"])
                    + " (se conserva el registro para reintentar)"
                )
            if (
                not plan["killed"]
                and not plan["dead"]
                and not plan["stubborn"]
                and not plan["duplicates"]
            ):
                print("Nada que limpiar.")
    return 0
