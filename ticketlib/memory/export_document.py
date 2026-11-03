"""Documento de snapshot, checksum y escritura atómica de memoria.

El snapshot JSON es la fuente de verdad: incluye memorias, playbooks, ficheros,
evidencia, propuestas y relaciones, con versión y checksum determinista. El
Markdown es una vista humana que embebe el mismo JSON en un comentario, así que
también se puede reimportar sin pérdida.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import sqlite3
import tempfile
from pathlib import Path
from typing import Any

from ..domain import ValidationError
from ..util import check_enum, now, parse_datetime
from . import core
from .config import DEFAULT_MEMORY_CONFIG, validate_memory_config
from .models import (
    MEMORY_CODE_RE,
    MEMORY_KINDS,
    MEMORY_SCOPES,
    MEMORY_SOURCES,
    MEMORY_STATUSES,
    PLAYBOOK_DESCRIPTION_CHARS,
    PLAYBOOK_KIND,
    normalize_fingerprint,
)
from .security import SECRET_PATTERNS, validate_memory_paths

VERSION = 1
FORMAT = "taiki-memory"
_MARKER = "<!-- taiki-memory-json:"


def _scrub(value: Any) -> str:
    """Sustituye patrones con forma de secreto antes de exportar."""
    text = str(value if value is not None else "")
    for _, pattern in SECRET_PATTERNS:
        text = pattern.sub("[redactado]", text)
    return text


def _checksum(memories: list[dict[str, Any]]) -> str:
    payload = json.dumps(
        {"version": VERSION, "memories": memories},
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    return "sha256:" + hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _code_map(conn: sqlite3.Connection) -> dict[int, str]:
    return {
        int(row["id"]): str(row["code"])
        for row in conn.execute("SELECT id, code FROM memories")
    }


def _memory_document(
    row: sqlite3.Row,
    codes: dict[int, str],
    evidence: list[dict[str, Any]],
    files: list[dict[str, Any]],
) -> dict[str, Any]:
    data = dict(row)
    supersedes = data.get("supersedes_id")
    return {
        "code": str(data["code"]),
        "scope": str(data["scope"]),
        "kind": str(data["kind"]),
        "key": _scrub(data.get("key")),
        "title": _scrub(data.get("title")),
        "description": _scrub(data.get("description")),
        "content": _scrub(data.get("content")),
        "paths": list(core.load_list(data.get("paths"))),
        "kinds": list(core.load_list(data.get("kinds"))),
        "ticket_kinds": list(core.load_list(data.get("ticket_kinds"))),
        "status": str(data["status"]),
        "confidence": float(data.get("confidence") or 0.0),
        "source": str(data.get("source") or "agent"),
        "actor": None if data.get("actor") is None else _scrub(data.get("actor")),
        "session_id": (
            None if data.get("session_id") is None else _scrub(data.get("session_id"))
        ),
        "ticket_id": (
            None if data.get("ticket_id") is None else _scrub(data.get("ticket_id"))
        ),
        "supersedes_code": (
            codes.get(int(supersedes)) if supersedes is not None else None
        ),
        "superseded_by": (
            None if data.get("superseded_by") is None else _scrub(data.get("superseded_by"))
        ),
        "pinned": bool(data.get("pinned")),
        "locked": bool(data.get("locked")),
        "use_count": int(data.get("use_count") or 0),
        "helpful_count": int(data.get("helpful_count") or 0),
        "unhelpful_count": int(data.get("unhelpful_count") or 0),
        "last_used_at": data.get("last_used_at"),
        "last_briefed_at": data.get("last_briefed_at"),
        "created_at": data.get("created_at"),
        "updated_at": data.get("updated_at"),
        "archived_at": data.get("archived_at"),
        "fingerprint": str(data.get("fingerprint") or ""),
        "evidence": [
            {
                "kind": str(item["kind"]),
                "ref": _scrub(item["ref"]),
                "created_at": item.get("created_at"),
            }
            for item in evidence
        ],
        "files": [
            {
                "path": str(item["path"]),
                "content": _scrub(item.get("content")),
                "sha256": str(item.get("sha256") or ""),
            }
            for item in files
        ],
    }


def _document(conn: sqlite3.Connection) -> dict[str, Any]:
    codes = _code_map(conn)
    memories: list[dict[str, Any]] = []
    for row in conn.execute("SELECT * FROM memories ORDER BY code"):
        memory_id = int(row["id"])
        evidence = [
            dict(item)
            for item in conn.execute(
                "SELECT kind, ref, created_at FROM memory_evidence "
                "WHERE memory_id = ? ORDER BY kind, ref",
                (memory_id,),
            )
        ]
        files = [
            dict(item)
            for item in conn.execute(
                "SELECT path, content, sha256 FROM memory_files "
                "WHERE memory_id = ? ORDER BY path",
                (memory_id,),
            )
        ]
        memories.append(_memory_document(row, codes, evidence, files))
    return {
        "format": FORMAT,
        "version": VERSION,
        "generated_at": now(),
        "checksum": _checksum(memories),
        "memories": memories,
    }


def _json_text(document: dict[str, Any]) -> str:
    return (
        json.dumps(document, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    )


def _markdown(document: dict[str, Any]) -> str:
    lines = [
        "# Taiki memory snapshot",
        "",
        f"- format: {document['format']}",
        f"- version: {document['version']}",
        f"- checksum: {document['checksum']}",
        f"- generated_at: {document['generated_at']}",
        f"- memories: {len(document['memories'])}",
        "",
    ]
    for memory in document["memories"]:
        lines.append(
            f"## {memory['code']} · {memory['title']} "
            f"[{memory['status']}] {memory['scope']}/{memory['kind']}"
        )
        if memory.get("description"):
            lines.extend(["", str(memory["description"]), ""])
        lines.extend(["", "```text", str(memory.get("content") or ""), "```", ""])
        if memory.get("evidence"):
            lines.append("Evidencia:")
            for item in memory["evidence"]:
                lines.append(f"- {item['kind']}:{item['ref']}")
            lines.append("")
        if memory.get("files"):
            lines.append("Ficheros:")
            for item in memory["files"]:
                lines.append(f"- {item['path']} ({item['sha256']})")
                lines.extend(["", "```text", item["content"], "```"])
            lines.append("")
    encoded = base64.b64encode(
        _json_text(document).encode("utf-8")
    ).decode("ascii")
    lines.extend(["", _MARKER, encoded, "-->"])
    return "\n".join(lines).rstrip() + "\n"


def _write_atomic(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    handle, temp_path = tempfile.mkstemp(
        prefix=f".{path.name}-", suffix=".tmp", dir=str(path.parent)
    )
    try:
        with open(handle, "w", encoding="utf-8", closefd=True) as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        Path(temp_path).replace(path)
    except BaseException:
        try:
            Path(temp_path).unlink()
        except OSError:
            pass
        raise


def _format_for(path: Path, value: str | None) -> str:
    if value:
        return value
    if path.suffix.lower() in (".md", ".markdown"):
        return "markdown"
    return "json"


def export_snapshot(
    conn: sqlite3.Connection,
    path: str | Path,
    format: str | None = None,
) -> dict[str, Any]:
    """Exporta un snapshot determinista y devuelve sus metadatos."""
    target = Path(path).expanduser()
    kind = _format_for(target, format)
    if kind not in ("json", "markdown"):
        raise ValidationError(
            f"Formato de exportación inválido: {kind!r} (usa json o markdown)"
        )
    document = _document(conn)
    text = _markdown(document) if kind == "markdown" else _json_text(document)
    _write_atomic(target, text)
    return {
        "path": str(target),
        "format": kind,
        "version": VERSION,
        "checksum": document["checksum"],
        "memories": len(document["memories"]),
        "playbooks": sum(
            1 for item in document["memories"] if item["kind"] == PLAYBOOK_KIND
        ),
        "bytes": len(text.encode("utf-8")),
    }


def _read_document(path: Path) -> dict[str, Any]:
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise ValidationError(f"No se pudo leer el snapshot {path}: {exc}") from exc
    if not text.lstrip().startswith("{") and (
        path.suffix.lower() in (".md", ".markdown") or _MARKER in text
    ):
        if _MARKER not in text:
            raise ValidationError(
                f"El Markdown {path} no incluye el snapshot JSON embebido"
            )
        chunk = text.rsplit(_MARKER, 1)[1]
        encoded = chunk.split("-->", 1)[0].strip()
        try:
            text = base64.b64decode(encoded).decode("utf-8")
        except (ValueError, UnicodeDecodeError) as exc:
            raise ValidationError(
                f"El snapshot embebido en {path} está corrupto: {exc}"
            ) from exc
    try:
        document = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ValidationError(f"JSON de snapshot inválido en {path}: {exc}") from exc
    if not isinstance(document, dict):
        raise ValidationError(f"El snapshot {path} debe ser un objeto JSON")
    return document


def _validated_document(path: Path) -> dict[str, Any]:
    document = _read_document(path)
    if document.get("format") != FORMAT:
        raise ValidationError(
            f"El snapshot {path} no es de Taiki (format={document.get('format')!r})"
        )
    if document.get("version") != VERSION:
        raise ValidationError(
            f"Versión de snapshot no soportada: {document.get('version')!r}"
        )
    memories = document.get("memories")
    if not isinstance(memories, list) or not all(
        isinstance(item, dict) for item in memories
    ):
        raise ValidationError(f"El snapshot {path} no contiene una lista de memorias")
    expected = _checksum(memories)
    if document.get("checksum") != expected:
        raise ValidationError(
            f"Checksum del snapshot inválido en {path}: "
            f"{document.get('checksum')!r} != {expected!r}"
        )
    return document


def _string(value: Any, field: str, *, required: bool = True) -> str:
    text = str(value if value is not None else "").strip()
    if required and not text:
        raise ValidationError(f"El snapshot no incluye {field}")
    return text


def _timestamp(value: Any) -> str:
    parsed = parse_datetime(value)
    if parsed is None:
        return now()
    return parsed.isoformat(timespec="seconds")


def _validated_memory(raw: dict[str, Any], cfg: dict[str, Any]) -> dict[str, Any]:
    code = _string(raw.get("code"), "código M-####")
    if not MEMORY_CODE_RE.fullmatch(code):
        raise ValidationError(f"Código de memoria inválido en snapshot: {code!r}")
    scope = _string(raw.get("scope"), "scope")
    kind = _string(raw.get("kind"), "kind")
    status = _string(raw.get("status"), "status")
    source = _string(raw.get("source"), "source")
    check_enum(scope, MEMORY_SCOPES, "scope")
    check_enum(kind, MEMORY_KINDS, "kind")
    check_enum(status, MEMORY_STATUSES, "status")
    check_enum(source, MEMORY_SOURCES, "source")
    title = core.clean_text(
        _string(raw.get("title"), "título"), "título", int(cfg["max_entry_chars"])
    )
    description_limit = (
        PLAYBOOK_DESCRIPTION_CHARS
        if kind == PLAYBOOK_KIND
        else int(cfg["max_entry_chars"])
    )
    description = core.clean_text(
        raw.get("description") or "",
        "descripción",
        description_limit,
        required=False,
    )
    content_limit = (
        int(cfg["max_playbook_chars"])
        if kind == PLAYBOOK_KIND
        else int(cfg["max_entry_chars"])
    )
    content = core.clean_text(
        _string(raw.get("content"), "contenido"), "contenido", content_limit
    )
    key = core.clean_key(
        _string(raw.get("key"), "clave") or normalize_fingerprint(title)[:80]
    )
    if kind == PLAYBOOK_KIND and not key.startswith("playbook:"):
        raise ValidationError(
            f"El playbook {code} necesita una clave playbook:<slug> en el snapshot"
        )
    paths = validate_memory_paths(raw.get("paths") or [])
    kinds = core.clean_kinds(raw.get("kinds") or [])
    ticket_kinds = core.clean_ticket_kinds(raw.get("ticket_kinds") or [])
    evidence = core.clean_evidence(raw.get("evidence") or [])
    created = {
        (item["kind"], item["ref"]): _timestamp(item.get("created_at"))
        for item in raw.get("evidence") or []
        if isinstance(item, dict)
    }
    evidence_rows = [
        {
            "kind": item["kind"],
            "ref": item["ref"],
            "created_at": created.get((item["kind"], item["ref"]), now()),
        }
        for item in evidence
    ]
    files = core.clean_files(raw.get("files") or [], cfg)
    return {
        "code": code,
        "scope": scope,
        "kind": kind,
        "key": key,
        "title": title,
        "description": description,
        "content": content,
        "paths": paths,
        "kinds": kinds,
        "ticket_kinds": ticket_kinds,
        "status": status,
        "confidence": core.confidence(raw.get("confidence", 0.5)),
        "source": source,
        "actor": None if raw.get("actor") is None else str(raw["actor"])[:200],
        "session_id": (
            None if raw.get("session_id") is None else str(raw["session_id"])[:200]
        ),
        "ticket_id": None if raw.get("ticket_id") is None else str(raw["ticket_id"]),
        "supersedes_code": raw.get("supersedes_code") or None,
        "superseded_by": raw.get("superseded_by") or None,
        "pinned": bool(raw.get("pinned")),
        "locked": bool(raw.get("locked")),
        "use_count": max(0, int(raw.get("use_count") or 0)),
        "helpful_count": max(0, int(raw.get("helpful_count") or 0)),
        "unhelpful_count": max(0, int(raw.get("unhelpful_count") or 0)),
        "last_used_at": raw.get("last_used_at") or None,
        "last_briefed_at": raw.get("last_briefed_at") or None,
        "created_at": _timestamp(raw.get("created_at")),
        "updated_at": _timestamp(raw.get("updated_at")),
        "archived_at": raw.get("archived_at") or None,
        "fingerprint": normalize_fingerprint(title, content),
        "evidence": evidence_rows,
        "files": files,
    }
