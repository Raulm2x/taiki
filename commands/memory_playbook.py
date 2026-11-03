"""Subcomandos de playbooks de memoria."""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any

from ticketlib import render, workspace
from ticketlib.domain import ValidationError
from ticketlib.memory import (
    archive_playbook,
    create_playbook,
    export_all,
    export_playbook,
    get_playbook,
    list_playbooks,
)
from ticketlib.memory.playbooks import slug_of

from .memory_common import _as_json, _clip, _session

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
