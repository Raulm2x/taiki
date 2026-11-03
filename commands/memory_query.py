"""Subcomandos de consulta, revisión y curado de memoria."""

from __future__ import annotations

import argparse

from ticketlib import render, workspace
from ticketlib.domain import ValidationError
from ticketlib.memory import (
    generate_candidates,
    get_memory,
    list_memories,
    review_packet,
)
from ticketlib.memory import budget as memory_budget
from ticketlib.memory import core as memory_core
from ticketlib.memory.curate import auto_curate
from ticketlib.memory.render import memory_summary
from ticketlib.memory.review import run_review_command
from ticketlib.memory.stats import impact, stats

from .memory_common import (
    _as_json,
    _memory_diff,
    _parse_since,
    _print_diff,
    _print_memory,
    _session,
    _write_review,
)


def _candidates(conn, args: argparse.Namespace) -> int:
    if args.limit is not None and args.limit <= 0:
        raise ValidationError("--limit debe ser mayor que 0")
    cfg = memory_core.config(None)
    # Con --kind o --all se genera sin el techo habitual y se recorta después
    # de filtrar: así ``--kind`` no se queda con una lista vacía por el límite.
    raw_limit = 100000 if (args.all or args.kind) else args.limit
    rows = generate_candidates(
        conn,
        cfg,
        since=_parse_since(args.since),
        limit=raw_limit,
    )
    if args.kind:
        rows = [row for row in rows if row["kind_sugerido"] == args.kind]
    if not args.all:
        cap = args.limit if args.limit is not None else int(cfg["candidate_max"])
        rows = rows[:cap]
    if _as_json(args):
        print(render.to_json({"count": len(rows), "candidates": rows}))
        return 0
    if not rows:
        print("No hay candidatos con esos filtros.")
        return 0
    for candidate in rows:
        print(
            f"{candidate['confidence']:.2f}  {candidate['rule']}  "
            f"{candidate['kind_sugerido']}  {candidate['title']}"
        )
        print(f"  {candidate['summary']}")
        evidence = ", ".join(
            f"{item['kind']}:{item['ref']}" for item in candidate["evidence"]
        )
        if evidence:
            print(f"  Evidencia: {evidence}")
        route = (
            candidate["details"].get("path_prefix")
            or (candidate["paths"][0] if candidate["paths"] else "—")
        )
        print(f"  Muestra: {candidate['sample_size']} · Ruta: {route}")
    return 0


def _review(conn, args: argparse.Namespace) -> int:
    cfg = memory_core.config(None)
    packet = review_packet(conn, cfg)
    if getattr(args, "run_command", False):
        packet["command"] = run_review_command(
            conn,
            cfg,
            dry_run=bool(getattr(args, "dry_run", False)),
        )
    if args.write:
        target = _write_review(workspace.project_root(), packet)
        packet["path"] = str(target)
    if _as_json(args):
        print(render.to_json(packet))
        return 0
    print(f"Revisión de memoria · {packet['generated_at']}")
    print(f"Candidatos: {len(packet['candidates'])}")
    for candidate in packet["candidates"][:5]:
        print(
            f"  {candidate['confidence']:.2f}  {candidate['rule']}  "
            f"{candidate['title']}"
        )
    print(f"Memorias vigentes: {len(packet['current_memories'])}")
    print(f"Tickets recientes: {len(packet['recent_tickets'])}")
    print(
        "Umbrales: "
        + ", ".join(f"{key}={value}" for key, value in packet["thresholds"].items())
    )
    print("Instrucciones:")
    for instruction in packet["instructions"]:
        print(f"  - {instruction}")
    if "command" in packet:
        report = packet["command"]
        if report["configured"]:
            print(
                f"review_command: {report['operations']} operación(es) → "
                f"{len(report['proposed'])} propuesta(s)"
            )
        else:
            print("review_command: no configurado")
    if args.write:
        print(f"Escrito: {packet['path']}")
    return 0


def _curate(conn, args: argparse.Namespace) -> int:
    outcome = auto_curate(
        conn,
        memory_core.config(None),
        dry_run=bool(getattr(args, "dry_run", False)),
        force=True,
    )
    result = outcome["result"]
    if _as_json(args):
        print(render.to_json(result))
        return 0
    print("Curado de memoria" + (" (dry-run)" if args.dry_run else ""))
    print(f"Stale: {len(result['stale'])}")
    if result["stale"]:
        print("  " + ", ".join(result["stale"]))
    print(f"Archivadas: {len(result['archived'])}")
    if result["archived"]:
        print("  " + ", ".join(result["archived"]))
    print(f"Merges: {len(result['merged'])}")
    for item in result["merged"]:
        print(f"  {item['code']} → {item['into']}")
    print("Presupuesto:")
    for scope in ("project", "user"):
        data = result["budget"][scope]
        estado = " · EXCEDIDO" if data["over"] else ""
        print(
            f"  {scope}: {data['used']}/{data['limit']} chars · "
            f"retiradas {len(data['retired'])}{estado}"
        )
    return 0


def _stats(conn, args: argparse.Namespace) -> int:
    data = stats(conn, memory_core.config(None))
    if _as_json(args):
        print(render.to_json(data))
        return 0
    print(f"Memorias: {data['total']}")
    print(
        "  estado: "
        + ", ".join(f"{name}={value}" for name, value in data["by_status"].items())
    )
    print(
        "  scope: "
        + ", ".join(f"{name}={value}" for name, value in data["by_scope"].items())
    )
    print(
        "  kind: "
        + ", ".join(f"{name}={value}" for name, value in data["by_kind"].items())
    )
    print("Presupuesto:")
    for scope in ("project", "user"):
        item = data["budget"][scope]
        print(
            f"  {scope}: {item['used']}/{item['limit']} chars · "
            f"quedan {item['remaining']} · {item['entries']} entrada(s)"
        )
    proposals = data["proposals"]
    print(f"Propuestas: {proposals['count']}")
    if proposals["codes"]:
        print("  " + ", ".join(proposals["codes"]))
    usage = data["usage"]
    print(
        f"Uso: {usage['events']} evento(s) · use_count {usage['use_count']} · "
        f"útil {usage['helpful']} · no útil {usage['unhelpful']}"
    )
    routing = data["routing"]
    print(f"Routing observado: {len(routing['groups'])} grupo(s)")
    for group in routing["groups"][:5]:
        print(
            f"  {group['kind']} · {group['profile']}: "
            f"{group['samples']} cierre(s), first-pass "
            f"{group['first_pass_rate']:.0%}, intentos {group['avg_attempts']}"
        )
    return 0


def _impact(conn, args: argparse.Namespace) -> int:
    data = impact(conn, memory_core.config(None), args.days)
    if _as_json(args):
        print(render.to_json(data))
        return 0
    print(
        f"Impacto · últimos {data['days']} días · "
        f"{data['closed']} ticket(s) cerrado(s)"
    )
    print(
        f"Intentos: media {data['attempts']['avg']} · "
        f"total {data['attempts']['total']}"
    )
    lead = data["lead_time_hours"]
    print(f"Lead time: media {lead['avg']} h ({lead['samples']} muestra(s))")
    verification = data["verification"]
    print(
        f"Verificación: {verification['verified']} verificaciones · "
        f"{verification['failures']} fallo(s)"
    )
    if data["trend"]:
        print("Tendencia semanal:")
        for item in data["trend"]:
            print(
                f"  {item['period']}: {item['closed']} cierre(s), "
                f"intentos {item['avg_attempts']}, "
                f"lead {item['avg_lead_time_hours']} h, "
                f"verificados {item['verified']}, "
                f"fallos {item['verification_failures']}"
            )
    print(f"Nota: {data['notes'][0]}")
    return 0


def _list(conn, args: argparse.Namespace) -> int:
    rows = list_memories(
        conn,
        scope=args.scope,
        status=args.status,
        kind=args.kind,
        ticket_kinds=args.ticket_kinds,
        limit=args.limit,
        include_inactive=args.status in ("archived", "superseded"),
    )
    if _as_json(args):
        print(render.to_json({"count": len(rows), "memories": rows}))
        return 0
    if not rows:
        print("No hay memorias con esos filtros.")
        return 0
    for memory in rows:
        print(
            f"{memory_summary(memory)}  ({memory_budget.entry_chars(memory)} chars)"
        )
    return 0


def _show(conn, args: argparse.Namespace) -> int:
    memory = get_memory(
        conn,
        args.code,
        session_id=_session(),
        record_usage=True,
    )
    memory["ledger"] = [
        dict(row)
        for row in conn.execute(
            "SELECT id, ts, actor, action, reason FROM memory_ledger "
            "WHERE memory_id = ? ORDER BY id DESC LIMIT 20",
            (memory["id"],),
        )
    ]
    memory["usage"] = [
        dict(row)
        for row in conn.execute(
            "SELECT ts, action, session_id, helpful, note FROM memory_usage "
            "WHERE memory_id = ? ORDER BY id DESC LIMIT 20",
            (memory["id"],),
        )
    ]
    cfg = memory_core.config(None)
    entries = memory_core.scope_entries(conn, memory["scope"])
    memory["budget"] = memory_budget.summary(
        entries, memory_core.limit_for(cfg, memory["scope"]), memory["scope"]
    )
    if _as_json(args):
        print(render.to_json(memory))
        return 0
    _print_memory(memory, False)
    files = memory.get("files") or []
    if files:
        print("  Ficheros: " + ", ".join(item["path"] for item in files))
    print(
        f"  Historial: {len(memory['ledger'])} asiento(s) · "
        f"uso: {len(memory['usage'])} registro(s) · "
        f"presupuesto: {memory['budget']['used']}/{memory['budget']['limit']}"
    )
    return 0


def _pending(conn, args: argparse.Namespace) -> int:
    rows = list_memories(conn, status="proposed", limit=args.limit)
    if getattr(args, "diff", False):
        for memory in rows:
            memory["diff"] = _memory_diff(conn, memory)
    if _as_json(args):
        print(render.to_json({"count": len(rows), "memories": rows}))
        return 0
    if not rows:
        print("No hay propuestas pendientes.")
        return 0
    for memory in rows:
        print(memory_summary(memory))
        if memory.get("content"):
            print(f"  {memory['content']}")
        if getattr(args, "diff", False):
            _print_diff(memory.get("diff") or [])
    return 0
