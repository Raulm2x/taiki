"""Comando ``taiki servers``: inventario y limpieza de servidores MCP."""

from __future__ import annotations

import argparse
import json
import os
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


def _project_active_pid() -> int | None:
    """PID activo según ``meta.mcp_pid`` de la DB del proyecto.

    ``runtime.register`` deja el PID del último MCP en el proyecto, no en el
    registro global que usa este comando: sin esta lectura no llegaría a
    ``servers.prune`` la evidencia que publica como informativa y que ``--force``
    usa para elegir a quién conservar. Si el proyecto no tiene DB accesible, no
    se falla el comando: se sigue con el resto de evidencias.
    """
    try:
        conn = db.connect()
        try:
            pid = db.meta_get(conn, "mcp_pid")
        finally:
            conn.close()
    except Exception:  # noqa: BLE001 - la limpieza no puede depender del proyecto
        return None
    return int(pid) if pid and pid.isdigit() else None


def cmd_servers(args: argparse.Namespace) -> int:
    if args.apply and not args.prune:
        print("Error: --apply requiere --prune", file=sys.stderr)
        return 2
    if args.duplicates and not args.prune:
        print("Error: --duplicates requiere --prune", file=sys.stderr)
        return 2
    if args.force and not args.prune:
        print("Error: --force requiere --prune", file=sys.stderr)
        return 2
    if args.force and not args.duplicates:
        print("Error: --force requiere --duplicates", file=sys.stderr)
        return 2
    if args.kill and not args.prune:
        print("Error: --kill requiere --prune", file=sys.stderr)
        return 2
    if args.kill and not args.duplicates:
        print("Error: --kill requiere --duplicates", file=sys.stderr)
        return 2
    if args.dry_run and not args.reload:
        print("Error: --dry-run requiere --reload", file=sys.stderr)
        return 2
    if args.no_wait and not args.reload:
        print("Error: --no-wait requiere --reload", file=sys.stderr)
        return 2
    if args.reload and args.prune:
        print(
            "Error: --reload y --prune son operaciones distintas", file=sys.stderr
        )
        return 2
    conn = db.connect(workspace.registry_path())
    try:
        entries = servers.inventory(conn)
        if args.kill:
            states = {item["pid"]: item["state"] for item in entries}
            own = os.getpid()
            invalid = [
                pid
                for pid in args.kill
                if pid != own
                and states.get(pid) not in ("duplicado", "huérfano")
            ]
            if invalid:
                print(
                    "Error: --kill solo admite PIDs duplicados u huérfanos del "
                    "inventario: "
                    + ", ".join(str(pid) for pid in invalid),
                    file=sys.stderr,
                )
                return 2
        plan = (
            servers.prune(
                conn,
                apply=args.apply,
                duplicates=args.duplicates,
                force=args.force,
                kill=args.kill,
                active_pid=(
                    _project_active_pid()
                    if args.duplicates
                    else None
                ),
            )
            if args.prune
            else None
        )
        reload_plan = (
            servers.reload_processes(
                conn,
                apply=not args.dry_run,
                wait=not args.no_wait,
                timeout=min(max(args.wait_timeout, 0.0), 60.0),
            )
            if args.reload
            else None
        )
    finally:
        conn.close()

    if args.json:
        payload = {"servers": entries, "prune": plan}
        if reload_plan is not None:
            payload["reload"] = reload_plan
        print(json.dumps(payload, ensure_ascii=False))
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
        for item in plan.get("active") or []:
            print(
                f"Activo (informativo): PID {item['pid']} "
                f"(evidencia: {item['evidence']}); con --force elige a quién "
                "conservar, sin --force no decide qué se termina"
            )
        for warning in plan.get("warnings") or []:
            print(f"Aviso: {warning}")
        deferred = plan.get("deferred") or []
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
            if deferred:
                print(
                    "Duplicados vivos que se conservan: "
                    + ", ".join(str(pid) for pid in deferred)
                )
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
            if deferred:
                print(
                    "Duplicados vivos conservados (no terminados): "
                    + ", ".join(str(pid) for pid in deferred)
                )
            if (
                not plan["killed"]
                and not plan["dead"]
                and not plan["stubborn"]
                and not plan["duplicates"]
                and not deferred
            ):
                print("Nada que limpiar.")

    if reload_plan is not None:
        statuses = reload_plan.get("statuses") or {}
        if statuses:
            counts = {
                state: sum(1 for value in statuses.values() if value == state)
                for state in servers.RELOAD_WAITED
            }
            print(
                f"Recarga: señalados: {len(reload_plan['signalled'])} · "
                f"recargados: {counts['recargado']} · "
                f"pendientes: {counts['pendiente']} · "
                f"sin respuesta: {counts['sin respuesta']} · "
                f"omitidos: {len(reload_plan['skipped'])}"
            )
            for pid in reload_plan["signalled"]:
                print(f"  PID {pid}: {statuses.get(pid, 'sin estado')}")
        else:
            print(
                f"Recarga: señalados: {len(reload_plan['signalled'])} · "
                f"omitidos: {len(reload_plan['skipped'])} · "
                f"{reload_plan['reason']}"
            )
            if reload_plan["signalled"]:
                print(
                    "  PIDs: "
                    + ", ".join(str(pid) for pid in reload_plan["signalled"])
                    + (" (dry-run; usa --reload sin --dry-run para señalar)"
                       if args.dry_run else "")
                )
    return 0
