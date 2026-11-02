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
from ticketlib.memory.playbooks import slug_of
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
    packet = review_packet(conn, memory_core.config(None))
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
    if args.write:
        print(f"Escrito: {packet['path']}")
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
