"""Comandos ``taiki memory`` y el atajo ``taiki remember``."""

from __future__ import annotations

import argparse
import json
import os
import re
import tempfile
from datetime import timedelta
from pathlib import Path
from typing import Any

from ticketlib import render, workspace
from ticketlib.domain import ValidationError
from ticketlib.memory import (
    add_memory,
    approve_memory,
    archive_memory,
    archive_playbook,
    create_playbook,
    export_all,
    export_playbook,
    generate_candidates,
    get_memory,
    get_playbook,
    list_memories,
    list_playbooks,
    patch_memory,
    pin_memory,
    reject_memory,
    review_packet,
)
from ticketlib.memory import budget as memory_budget
from ticketlib.memory import core as memory_core
from ticketlib.memory.curate import auto_curate
from ticketlib.memory.export import (
    export_snapshot,
    import_snapshot,
    rollback as memory_rollback,
)
from ticketlib.memory.playbooks import slug_of
from ticketlib.memory.review import run_review_command
from ticketlib.memory.stats import impact, stats
from ticketlib.memory.render import memory_summary
from ticketlib.util import now, parse_datetime

from .common import _conn

_EDIT_FIELDS = (
    "title",
    "content",
    "description",
    "key",
    "kind",
    "confidence",
    "ticket_kinds",
)
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

# ``--support`` clasifica por extensión; el resto son referencias de texto.
_SUPPORT_PREFIXES = {
    ".sh": "scripts/",
    ".bash": "scripts/",
    ".zsh": "scripts/",
    ".py": "scripts/",
    ".rb": "scripts/",
    ".pl": "scripts/",
    ".lua": "scripts/",
    ".js": "scripts/",
    ".ts": "scripts/",
    ".png": "assets/",
    ".jpg": "assets/",
    ".jpeg": "assets/",
    ".gif": "assets/",
    ".svg": "assets/",
    ".webp": "assets/",
    ".ico": "assets/",
    ".tmpl": "templates/",
    ".tpl": "templates/",
    ".template": "templates/",
}


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


def _add(conn, args: argparse.Namespace) -> int:
    memory = add_memory(
        conn,
        title=args.title,
        content=args.content,
        kind=args.kind,
        key=args.key,
        scope=args.scope,
        confidence=args.confidence,
        source="user",
        actor="usuario",
        session_id=_session(),
        evidence=list(args.evidence or []),
        ticket_kinds=args.ticket_kinds,
        pinned=bool(args.pin),
    )
    _print_memory(memory, _as_json(args))
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


def _approve(conn, args: argparse.Namespace) -> int:
    _require_user(args.actor)
    memory = approve_memory(
        conn,
        args.code,
        actor="usuario",
        session_id=_session(),
        reason=args.why,
    )
    _print_memory(memory, _as_json(args))
    return 0


def _reject(conn, args: argparse.Namespace) -> int:
    _require_user(args.actor)
    memory = reject_memory(
        conn,
        args.code,
        actor="usuario",
        session_id=_session(),
        reason=args.why or "rechazada",
    )
    _print_memory(memory, _as_json(args))
    return 0


def _pin(conn, args: argparse.Namespace, value: bool) -> int:
    memory = pin_memory(
        conn,
        args.code,
        pinned=value,
        actor="usuario",
        session_id=_session(),
    )
    _print_memory(memory, _as_json(args))
    return 0


def _rm(conn, args: argparse.Namespace) -> int:
    memory = archive_memory(
        conn,
        args.code,
        actor="usuario",
        session_id=_session(),
        reason=args.why,
    )
    if _as_json(args):
        print(render.to_json(memory))
    else:
        print(
            f"{memory['code']} archivada (no borrada). "
            "El histórico queda en el ledger."
        )
    return 0


def _edit(conn, args: argparse.Namespace) -> int:
    updates = {
        name: getattr(args, name)
        for name in _EDIT_FIELDS
        if getattr(args, name) is not None
    }
    if args.paths is not None:
        updates["paths"] = args.paths
    if not updates:
        raise ValidationError("Indica al menos un campo para editar")
    memory = patch_memory(
        conn,
        args.code,
        actor="usuario",
        session_id=_session(),
        reason=args.why,
        **updates,
    )
    _print_memory(memory, _as_json(args))
    return 0


def _read_text(path: Path, label: str) -> str:
    """Lee un fichero de texto y convierte errores de disco en validación."""
    try:
        return path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        raise ValidationError(f"No se pudo leer {label}: {exc}") from exc


def _support_payloads(sources: list[str] | None) -> list[dict[str, str]]:
    """Convierte ``--support <ruta>`` en ficheros internos del playbook.

    La ruta de destino se deduce de la extensión: scripts ejecutables,
    imágenes en assets, plantillas y el resto en references. El nombre se
    conserva y el núcleo valida el prefijo, el tamaño y los secretos.
    """
    payloads: list[dict[str, str]] = []
    seen: set[str] = set()
    for source in sources or []:
        path = Path(source).expanduser()
        if not path.is_file():
            raise ValidationError(f"Fichero de apoyo no encontrado: {source}")
        target = _SUPPORT_PREFIXES.get(path.suffix.lower(), "references/") + path.name
        if target in seen:
            raise ValidationError(
                f"Dos ficheros de apoyo colisionan en la misma ruta: {target}"
            )
        seen.add(target)
        payloads.append({"path": target, "content": _read_text(path, source)})
    return payloads


def _print_playbook(memory: dict[str, Any], as_json: bool) -> None:
    if as_json:
        print(render.to_json(memory))
        return
    slug = slug_of(memory) or "?"
    print(
        f"{memory['code']}  {memory['status']}  playbook/{slug}  "
        f"{memory['title']}"
    )
    if memory.get("description"):
        print(f"  {memory['description']}")
    content = str(memory.get("content") or "").rstrip()
    if content:
        print()
        print(content)
        print()
    evidence = memory.get("evidence") or []
    if evidence:
        print(
            "  Evidencia: "
            + ", ".join(f"{item['kind']}:{item['ref']}" for item in evidence)
        )
    files = memory.get("files") or []
    if files:
        print("  Ficheros:")
        for item in files:
            digest = str(item.get("sha256") or "")[:8]
            print(f"    - {item['path']} ({digest})")
    for warning in memory.get("warnings") or []:
        print(f"  Aviso: {warning}")


def _playbook_add(conn, args: argparse.Namespace) -> int:
    content = args.content
    if args.file:
        content = _read_text(Path(args.file).expanduser(), args.file)
    memory = create_playbook(
        conn,
        slug=args.slug,
        title=args.title,
        description=args.description,
        content=content,
        evidence=list(args.evidence or []),
        files=_support_payloads(args.support),
        confidence=args.confidence,
        source="user",
        actor="usuario",
        session_id=_session(),
    )
    _print_playbook(memory, _as_json(args))
    return 0


def _playbook_list(conn, args: argparse.Namespace) -> int:
    rows = list_playbooks(
        conn,
        status=args.status,
        include_inactive=bool(
            args.all or args.status in ("archived", "superseded")
        ),
        limit=args.limit,
    )
    if _as_json(args):
        print(render.to_json({"count": len(rows), "playbooks": rows}))
        return 0
    if not rows:
        print("No hay playbooks con esos filtros.")
        return 0
    for memory in rows:
        slug = slug_of(memory) or "?"
        description = _clip(memory.get("description"))
        print(
            f"{memory['code']}  {memory['status']}  {slug}  {memory['title']}"
        )
        if description:
            print(f"  {description}")
    return 0


def _playbook_show(conn, args: argparse.Namespace) -> int:
    memory = get_playbook(conn, args.ref)
    _print_playbook(memory, _as_json(args))
    return 0


def _playbook_export(conn, args: argparse.Namespace) -> int:
    if args.all and args.ref:
        raise ValidationError("Usa --all o un slug, no ambos")
    if not args.all and not args.ref:
        raise ValidationError("Indica un slug o usa --all")
    root = workspace.project_root()
    if args.all:
        result: Any = export_all(conn, root)
    else:
        result = export_playbook(root, get_playbook(conn, args.ref))
    if _as_json(args):
        print(render.to_json(result))
        return 0
    if args.all:
        print(
            f"Exportados {len(result['exported'])} playbook(s) a "
            f"{result['root']}"
        )
        for slug in result["exported"]:
            print(f"  - {slug}")
        for slug in result["removed"]:
            print(f"  retirado: {slug}")
        return 0
    print(f"Exportado {result['slug']} en {result['path']}")
    for path in result["files"]:
        print(f"  - {path}")
    for path in result["removed"]:
        print(f"  retirado: {path}")
    return 0


def _playbook_archive(conn, args: argparse.Namespace) -> int:
    memory = archive_playbook(
        conn,
        args.ref,
        actor="usuario",
        session_id=_session(),
        reason=args.why,
    )
    if _as_json(args):
        print(render.to_json(memory))
    else:
        print(
            f"{memory['code']} archivado (no borrado). "
            "El histórico queda en el ledger."
        )
    return 0


def _playbook(conn, args: argparse.Namespace) -> int:
    """Despacha ``taiki memory playbook``."""
    action = args.playbook_action
    if action == "add":
        return _playbook_add(conn, args)
    if action == "list":
        return _playbook_list(conn, args)
    if action == "show":
        return _playbook_show(conn, args)
    if action == "export":
        return _playbook_export(conn, args)
    if action == "archive":
        return _playbook_archive(conn, args)
    raise ValidationError(f"Acción de playbook desconocida: {action}")


def cmd_memory(args: argparse.Namespace) -> int:
    """Despacha los subcomandos de ``taiki memory``."""
    action = args.memory_action
    with _conn() as conn:
        if action == "add":
            return _add(conn, args)
        if action == "list":
            return _list(conn, args)
        if action == "show":
            return _show(conn, args)
        if action == "pending":
            return _pending(conn, args)
        if action == "candidates":
            return _candidates(conn, args)
        if action == "review":
            return _review(conn, args)
        if action == "curate":
            return _curate(conn, args)
        if action == "stats":
            return _stats(conn, args)
        if action == "impact":
            return _impact(conn, args)
        if action == "export":
            return _export(conn, args)
        if action == "import":
            return _import(conn, args)
        if action == "rollback":
            return _rollback(conn, args)
        if action == "approve":
            return _approve(conn, args)
        if action == "reject":
            return _reject(conn, args)
        if action == "pin":
            return _pin(conn, args, True)
        if action == "unpin":
            return _pin(conn, args, False)
        if action == "rm":
            return _rm(conn, args)
        if action == "edit":
            return _edit(conn, args)
        if action == "playbook":
            return _playbook(conn, args)
    raise ValidationError(f"Acción de memoria desconocida: {action}")


def cmd_remember(args: argparse.Namespace) -> int:
    """Atajo para una preferencia activa del usuario."""
    text = " ".join(args.text).strip()
    if not text:
        raise ValidationError("El texto de remember no puede estar vacío")
    title = args.title or text[:80]
    if args.ticket:
        evidence = [{"kind": "ticket", "ref": args.ticket}]
    else:
        evidence = [{"kind": "session", "ref": _session() or "ses_cli"}]
    with _conn() as conn:
        memory = add_memory(
            conn,
            title=title,
            content=text,
            kind="preference",
            scope="user",
            source="user",
            actor="usuario",
            session_id=_session(),
            evidence=evidence,
        )
    _print_memory(memory, _as_json(args))
    return 0


__all__ = ["cmd_memory", "cmd_remember"]
