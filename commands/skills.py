"""Comandos ``taiki skills``: sugerencias e instalación verificada de skills.

La instalación es exclusiva del usuario/CLI: descarga el bundle, verifica sus
hashes y deja el playbook en estado ``proposed``. Este módulo no activa nada;
la aprobación sigue siendo ``taiki memory approve``.
"""

from __future__ import annotations

import argparse
import os
import sys

from ticketlib import render, workspace
from ticketlib.domain import ValidationError
from ticketlib.memory.playbooks import export_all
from ticketlib.skills.catalog import suggest
from ticketlib.skills.detect import detect_techs
from ticketlib.skills.install import install_skill, list_skills, remove_skill
from ticketlib.skills.registry import (
    DEFAULT_REGISTRY_URL,
    default_fetch,
    load_registry,
)

from .common import _conn

REGISTRY_ENV = "TAIKI_SKILLS_REGISTRY"


def _session() -> str | None:
    return (
        os.environ.get("OPENCODE_SESSION_ID")
        or os.environ.get("AGY_SESSION_ID")
        or os.environ.get("SESSION_ID")
        or None
    )


def _as_json(args: argparse.Namespace) -> bool:
    return bool(getattr(args, "json", False))


def _root():
    """Raíz del proyecto donde viven la caché y las skills exportadas."""
    return workspace.project_root()


def _registry(root):
    """Carga el registro oficial o el fixture indicado por entorno."""
    source = os.environ.get(REGISTRY_ENV) or DEFAULT_REGISTRY_URL
    return load_registry(source, root=root)


def _fetch(url: str) -> bytes | str:
    """Fetch inyectable; los tests lo sustituyen por un mapa local."""
    return default_fetch(url)


def _limit(args: argparse.Namespace) -> int:
    raw = getattr(args, "limit", 20)
    limit = 20 if raw is None else int(raw)
    if limit <= 0:
        raise ValidationError("--limit debe ser mayor que 0")
    return limit


def _suggest(_conn, args: argparse.Namespace) -> int:
    limit = _limit(args)
    root = _root()
    techs = detect_techs(root)
    registry = _registry(root)
    rows = suggest(techs, registry, limit=limit)
    data = {
        "root": str(root),
        "techs": techs,
        "count": len(rows),
        "suggestions": rows,
    }
    if _as_json(args):
        print(render.to_json(data))
        return 0
    if not techs:
        print("No se detectó ningún stack conocido en el proyecto.")
        return 0
    print("Stack detectado: " + ", ".join(techs))
    if not rows:
        print("No hay skills curadas para ese stack en el registro.")
        return 0
    for item in rows:
        review = (item.get("review") or {}).get("status") or "-"
        security = (item.get("securityCheck") or {}).get("status") or "-"
        digest = str(item.get("hash") or "")[:12]
        print(
            f"{item['name']}  {item.get('source') or '-'}  {digest}  "
            f"review={review} security={security}"
        )
    print("Instala una con: taiki skills install <nombre>")
    return 0


def _confirm(name: str, yes: bool) -> bool:
    if yes:
        return True
    if not sys.stdin.isatty():
        raise ValidationError(
            "La instalación exige confirmación; repite con --yes en modo no "
            "interactivo."
        )
    answer = input(f"¿Instalar la skill {name}? [s/N] ").strip().lower()
    return answer in ("s", "si", "sí", "y", "yes")


def _install(conn, args: argparse.Namespace) -> int:
    name = str(args.name or "").strip()
    if not name:
        raise ValidationError("Indica el nombre de la skill a instalar")
    if not _confirm(name, bool(args.yes)):
        print("Instalación cancelada.")
        return 0
    root = _root()
    registry = _registry(root)
    result = install_skill(
        root,
        conn,
        registry,
        name,
        _fetch,
        actor="usuario",
        session_id=_session(),
    )
    if _as_json(args):
        print(render.to_json(result))
        return 0
    skill = result.get("skill") or {}
    print(
        f"{result['code']}  {result['status']}  {skill.get('slug') or name}  "
        f"{result['title']}"
    )
    commit = str(skill.get("commitSha") or "")[:12]
    bundle = str(skill.get("bundleHash") or "")[:12]
    print(f"  Fuente: {skill.get('source') or '-'} · commit {commit} · bundle {bundle}")
    if skill.get("url"):
        print(f"  Evidencia: {skill['url']}")
    print(f"  Aprobación pendiente: taiki memory approve {result['code']}")
    print(f"  Para descartarla: taiki memory reject {result['code']} --why \"...\"")
    return 0


def _list(conn, args: argparse.Namespace) -> int:
    rows = list_skills(conn)
    if _as_json(args):
        print(render.to_json({"count": len(rows), "skills": rows}))
        return 0
    if not rows:
        print("No hay skills instaladas.")
        return 0
    for item in rows:
        description = " ".join(str(item.get("description") or "").split())
        if len(description) > 100:
            description = description[:100].rstrip() + "…"
        print(
            f"{item['code']}  {item['status']}  {item['name']}  "
            f"{item['title']}  [{item['origin']}]"
        )
        if description:
            print(f"  {description}")
    return 0


def _remove(conn, args: argparse.Namespace) -> int:
    result = remove_skill(
        conn,
        args.name,
        actor="usuario",
        session_id=_session(),
        reason=args.why,
    )
    print(
        f"{result['code']} archivado (no borrado). "
        "El histórico queda en el ledger."
    )
    return 0


def _sync(conn, args: argparse.Namespace) -> int:
    root = _root()
    result = export_all(conn, root)
    if _as_json(args):
        print(render.to_json(result))
        return 0
    print(
        f"Exportados {len(result['exported'])} playbook(s) activos a "
        f"{result['root']}"
    )
    for slug in result["exported"]:
        print(f"  - {slug}")
    for slug in result["removed"]:
        print(f"  retirado: {slug}")
    return 0


def cmd_skills(args: argparse.Namespace) -> int:
    """Despacha los subcomandos de ``taiki skills``."""
    action = args.skills_action
    with _conn() as conn:
        if action == "suggest":
            return _suggest(conn, args)
        if action == "install":
            return _install(conn, args)
        if action == "list":
            return _list(conn, args)
        if action == "remove":
            return _remove(conn, args)
        if action == "sync":
            return _sync(conn, args)
    raise ValidationError(f"Acción de skills desconocida: {action}")


__all__ = ["REGISTRY_ENV", "cmd_skills"]
