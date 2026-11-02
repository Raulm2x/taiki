"""Playbooks: memoria procedimental exportable como skills.

Un playbook es una memoria de tipo ``playbook`` con clave ``playbook:<slug>``
cuyo cuerpo es el procedimiento y cuyos ficheros de apoyo viven en
``memory_files``. Se exporta a ``.taiki/generated/skills/<slug>/SKILL.md`` con
frontmatter compatible con agentskills/OpenCode; el cuerpo solo se carga bajo
demanda y el brief usa un índice acotado.
"""

from __future__ import annotations

import json
import os
import shutil
import sqlite3
import tempfile
from pathlib import Path
from typing import Any

from ..domain import ValidationError
from ..store.common import UNSET
from . import core
from . import security
from .config import load_memory_config
from .lifecycle import archive_memory, patch_memory
from .models import (
    PLAYBOOK_DESCRIPTION_CHARS,
    PLAYBOOK_KIND,
    PLAYBOOK_SLUG_MAX_CHARS,
    parse_playbook_key,
    playbook_key,
    validate_playbook_slug,
)
from .store import add_memory, list_memories

SKILL_FILE = "SKILL.md"
SKILLS_RELATIVE = Path(".taiki") / "generated" / "skills"
LIVE_ORDER = "'active', 'proposed', 'stale'"
EXPORTABLE_STATUSES = ("active",)


def _config(config: dict[str, Any] | None) -> dict[str, Any]:
    """Acepta el bloque memory, el config completo o nada."""
    if config is None:
        return load_memory_config()
    return core.config(config)


def slug_of(memory: dict[str, Any]) -> str | None:
    """Slug del playbook a partir de su clave, o ``None`` si no es playbook."""
    if memory.get("kind") != PLAYBOOK_KIND:
        return None
    return parse_playbook_key(memory.get("key"))


def _require_slug(memory: dict[str, Any]) -> str:
    slug = slug_of(memory)
    if slug is None:
        raise ValidationError(
            f"La memoria {memory.get('code')} no es un playbook con clave "
            "playbook:<slug>"
        )
    return slug


def _memory(conn: sqlite3.Connection, row: sqlite3.Row) -> dict[str, Any]:
    """Convierte una fila de playbook en dict con evidencia y ficheros.

    Los ficheros incluyen su contenido para que ``export_playbook`` funcione
    solo con el diccionario de la memoria.
    """
    data = core.row_to_memory(row, conn, details=True)
    contents = {
        item["path"]: item["content"] for item in support_files(conn, int(row["id"]))
    }
    data["files"] = [
        {
            "path": item["path"],
            "sha256": item.get("sha256"),
            "content": contents.get(item["path"], ""),
        }
        for item in data.get("files", [])
    ]
    return data


def create_playbook(
    conn: sqlite3.Connection,
    *,
    slug: str,
    title: str,
    description: str,
    content: str,
    evidence: Any,
    files: Any = None,
    paths: Any = None,
    confidence: float = 0.5,
    source: str = "agent",
    actor: str | None = None,
    session_id: str | None = None,
    ticket_id: str | None = None,
    status: str | None = None,
    pinned: bool = False,
    locked: bool = False,
    reason: str | None = None,
    config: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Crea un playbook validando slug, textos, files y evidencia."""
    cfg = _config(config)
    clean_slug = validate_playbook_slug(slug)
    name = core.clean_text(title, "nombre", int(cfg["max_entry_chars"]))
    summary = core.clean_text(
        description, "descripción", PLAYBOOK_DESCRIPTION_CHARS
    )
    body = core.clean_text(content, "contenido", int(cfg["max_playbook_chars"]))
    evidence_rows = core.clean_evidence(evidence)
    if not evidence_rows:
        raise ValidationError(
            "Un playbook exige al menos una evidencia (ticket, commit, file, "
            "url o session); sin ella no se guarda."
        )
    return add_memory(
        conn,
        title=name,
        description=summary,
        content=body,
        kind=PLAYBOOK_KIND,
        key=playbook_key(clean_slug),
        evidence=evidence_rows,
        files=files,
        paths=paths,
        confidence=confidence,
        source=source,
        actor=actor,
        session_id=session_id,
        ticket_id=ticket_id,
        status=status,
        pinned=pinned,
        locked=locked,
        reason=reason,
        config=cfg,
    )


def update_playbook(
    conn: sqlite3.Connection,
    ref: Any,
    *,
    title: Any = UNSET,
    description: Any = UNSET,
    content: Any = UNSET,
    files: Any = UNSET,
    actor: str | None = None,
    session_id: str | None = None,
    reason: str | None = None,
    config: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Actualiza un playbook sin cambiar su slug."""
    cfg = _config(config)
    memory = get_playbook(conn, ref)
    if description is not UNSET:
        description = core.clean_text(
            description, "descripción", PLAYBOOK_DESCRIPTION_CHARS
        )
    return patch_memory(
        conn,
        memory["code"],
        title=title,
        description=description,
        content=content,
        files=files,
        actor=actor,
        session_id=session_id,
        reason=reason,
        config=cfg,
    )


def get_playbook(conn: sqlite3.Connection, ref: Any) -> dict[str, Any]:
    """Resuelve un playbook por código M-#### o por slug."""
    if isinstance(ref, bool):
        raise ValidationError(f"Playbook inválido: {ref!r}")
    text = str(ref or "").strip()
    if isinstance(ref, int) or text.upper().startswith("M-"):
        row = core.memory_row(conn, ref)
    else:
        slug = validate_playbook_slug(text)
        row = conn.execute(
            "SELECT * FROM memories WHERE kind = ? AND \"key\" = ? "
            f"ORDER BY CASE WHEN status IN ({LIVE_ORDER}) THEN 0 ELSE 1 END, "
            "id DESC LIMIT 1",
            (PLAYBOOK_KIND, playbook_key(slug)),
        ).fetchone()
        if row is None:
            raise ValidationError(f"Playbook no encontrado: {text}")
    if row["kind"] != PLAYBOOK_KIND:
        raise ValidationError(f"La memoria {row['code']} no es un playbook")
    return _memory(conn, row)


def list_playbooks(
    conn: sqlite3.Connection,
    *,
    status: str | None = None,
    include_inactive: bool = False,
    limit: int = 50,
) -> list[dict[str, Any]]:
    """Lista playbooks por estado, sin contenido de apoyo."""
    return list_memories(
        conn,
        kind=PLAYBOOK_KIND,
        status=status,
        include_inactive=include_inactive,
        limit=limit,
    )


def archive_playbook(
    conn: sqlite3.Connection,
    ref: Any,
    *,
    actor: str | None = None,
    session_id: str | None = None,
    reason: str | None = None,
    config: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Archiva un playbook; el histórico queda en el ledger."""
    memory = get_playbook(conn, ref)
    return archive_memory(
        conn,
        memory["code"],
        actor=actor,
        session_id=session_id,
        reason=reason,
        config=_config(config),
    )


def support_files(
    conn: sqlite3.Connection,
    memory_id: int,
) -> list[dict[str, str]]:
    """Ficheros de apoyo con su contenido, ordenados por ruta."""
    rows = conn.execute(
        "SELECT path, content, sha256 FROM memory_files "
        "WHERE memory_id = ? ORDER BY path",
        (int(memory_id),),
    ).fetchall()
    return [dict(row) for row in rows]


def _yaml(value: object) -> str:
    """Valor YAML seguro usando la sintaxis de cadena de JSON."""
    return json.dumps(str(value if value is not None else ""), ensure_ascii=False)


def skill_text(memory: dict[str, Any], files: list[dict[str, str]]) -> str:
    """Renderiza el SKILL.md de un playbook con su frontmatter."""
    slug = _require_slug(memory)
    title = str(memory.get("title") or slug)
    description = str(memory.get("description") or "")
    evidence = memory.get("evidence") or []
    lines = [
        "---",
        f"name: {_yaml(slug)}",
        f"description: {_yaml(description)}",
        "metadata:",
        "  taiki:",
        f"    code: {_yaml(memory.get('code'))}",
    ]
    if evidence:
        lines.append("    evidence:")
        for item in evidence:
            label = f"{item.get('kind')}:{item.get('ref')}"
            lines.append(f"      - {_yaml(label)}")
    else:
        lines.append("    evidence: []")
    lines.extend(["---", "", f"# {title}", ""])
    body = str(memory.get("content") or "").rstrip()
    if body:
        lines.extend([body, ""])
    if files:
        lines.append("## Recursos")
        lines.append("")
        lines.extend(f"- {item['path']}" for item in files)
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def _write_atomic(path: Path, text: str) -> None:
    """Escribe un fichero de texto de forma atómica."""
    path.parent.mkdir(parents=True, exist_ok=True)
    handle, temp_path = tempfile.mkstemp(
        prefix=f".{path.name}-", suffix=".tmp", dir=str(path.parent)
    )
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as stream:
            stream.write(text)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp_path, path)
    except BaseException:
        try:
            os.unlink(temp_path)
        except OSError:
            pass
        raise


def _clean_support(folder: Path, expected: set[str]) -> list[str]:
    """Elimina ficheros de apoyo que ya no pertenecen al playbook."""
    removed: list[str] = []
    for prefix in security.ALLOWED_PATH_PREFIXES:
        base = folder / prefix.rstrip("/")
        if not base.is_dir():
            continue
        for path in sorted(base.rglob("*")):
            if not path.is_file():
                continue
            relative = path.relative_to(folder).as_posix()
            if relative not in expected:
                path.unlink()
                removed.append(relative)
    return removed


def export_playbook(
    root: str | Path,
    memory: dict[str, Any],
    *,
    conn: sqlite3.Connection | None = None,
    config: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Escribe ``.taiki/generated/skills/<slug>/SKILL.md`` y sus apoyos."""
    _config(config)
    slug = _require_slug(memory)
    memory_id = int(memory.get("id") or 0)
    if conn is not None:
        files = support_files(conn, memory_id)
    else:
        files = [
            {
                "path": security.validate_memory_path(item.get("path")),
                "content": str(item.get("content") or ""),
                "sha256": str(item.get("sha256") or ""),
            }
            for item in (memory.get("files") or [])
        ]
    text = skill_text(memory, files)
    folder = Path(root).expanduser() / SKILLS_RELATIVE / slug
    target = folder / SKILL_FILE
    _write_atomic(target, text)
    written: list[str] = []
    expected: set[str] = set()
    for item in files:
        relative = security.validate_memory_path(item["path"])
        path = folder / relative
        _write_atomic(path, item["content"])
        expected.add(relative)
        written.append(str(path))
    removed = _clean_support(folder, expected)
    return {
        "slug": slug,
        "code": memory.get("code"),
        "path": str(target),
        "files": written,
        "removed": removed,
        "chars": len(text),
    }


def export_all(
    conn: sqlite3.Connection,
    root: str | Path,
    *,
    config: dict[str, Any] | None = None,
    limit: int = 1000,
) -> dict[str, Any]:
    """Exporta playbooks activos y retira skills de playbooks obsoletos.

    Solo limpia dentro de ``.taiki/generated/skills`` y únicamente directorios
    cuyo nombre corresponde a una memoria ``playbook:<slug>`` que ya no está
    activa. Los directorios ajenos se conservan.
    """
    cfg = _config(config)
    root_path = Path(root).expanduser()
    every = list_memories(
        conn,
        kind=PLAYBOOK_KIND,
        include_inactive=True,
        limit=limit,
    )
    active = [item for item in every if item.get("status") in EXPORTABLE_STATUSES]
    exported: list[dict[str, Any]] = []
    for item in active:
        exported.append(
            export_playbook(
                root_path,
                get_playbook(conn, item["code"]),
                config=cfg,
            )
        )
    active_slugs = {
        slug for slug in (slug_of(item) for item in active) if slug is not None
    }
    obsolete = {
        slug
        for slug in (slug_of(item) for item in every)
        if slug is not None and slug not in active_slugs
    }
    removed: list[str] = []
    folder = root_path / SKILLS_RELATIVE
    if folder.is_dir():
        for child in sorted(folder.iterdir()):
            if child.is_dir() and child.name in obsolete:
                shutil.rmtree(child)
                removed.append(child.name)
    return {
        "root": str(folder),
        "exported": [item["slug"] for item in exported],
        "paths": [item["path"] for item in exported],
        "removed": removed,
    }


def brief_index(
    conn: sqlite3.Connection,
    config: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Índice compacto de playbooks activos para el brief.

    Respeta ``playbook_index_chars``: si una entrada no cabe, se omite en vez
    de truncar el brief. El cuerpo del playbook nunca se incluye.
    """
    cfg = _config(config)
    limit = int(cfg["playbook_index_chars"])
    rows = list_memories(conn, kind=PLAYBOOK_KIND, status="active", limit=1000)
    lines: list[str] = []
    entries: list[dict[str, Any]] = []
    used = 0
    for memory in rows:
        slug = slug_of(memory)
        if slug is None:
            continue
        title = str(memory.get("title") or slug)
        description = str(memory.get("description") or "").strip()
        line = f"- {slug} — {title}"
        if description:
            line += f": {description}"
        line += f" ({memory.get('code')})"
        size = len(line) + 1
        if used + size > limit:
            break
        used += size
        lines.append(line)
        entries.append(
            {
                "slug": slug,
                "title": title,
                "description": description,
                "code": memory.get("code"),
            }
        )
    text = ("\n".join(lines) + "\n") if lines else ""
    return {
        "text": text,
        "entries": entries,
        "chars": used,
        "limit": limit,
        "omitted": max(0, len(rows) - len(entries)),
    }


__all__ = [
    "EXPORTABLE_STATUSES",
    "PLAYBOOK_SLUG_MAX_CHARS",
    "SKILLS_RELATIVE",
    "SKILL_FILE",
    "archive_playbook",
    "brief_index",
    "create_playbook",
    "export_all",
    "export_playbook",
    "get_playbook",
    "list_playbooks",
    "skill_text",
    "slug_of",
    "support_files",
    "update_playbook",
    "validate_playbook_slug",
]
