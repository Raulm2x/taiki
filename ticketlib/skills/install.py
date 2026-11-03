"""Instalación verificada de skills curadas como playbooks propuestos.

Una skill descargada nunca se ejecuta ni se escribe como skill activa sin
aprobación: se guarda como memoria ``playbook`` con ``source=import`` y estado
``proposed``. El cuerpo de ``SKILL.md`` es el contenido del playbook, los
demás ficheros viajan como apoyos, y la evidencia conserva la URL de origen y
el commit del registro.
"""

from __future__ import annotations

import posixpath
import re
import sqlite3
from pathlib import Path
from typing import Any

from ..domain import ValidationError
from ..memory import playbooks
from ..memory.core import config as memory_config
from ..memory.models import (
    PLAYBOOK_DESCRIPTION_CHARS,
    PLAYBOOK_KIND,
    parse_playbook_key,
    playbook_key,
    validate_playbook_slug,
)
from ..memory.security import ALLOWED_PATH_PREFIXES, scan_content, validate_memory_path
from .registry import (
    RAW_BASE_URL,
    Fetch,
    fetch_bundle,
    skill_file_url,
)

LIVE_STATUSES = ("active", "proposed", "stale")
SKILL_FILE = "SKILL.md"
_FRONTMATTER_KEYS = ("name", "description")


def skill_slug(name: object) -> str:
    """Convierte un nombre de skill del registro en un slug de playbook."""
    text = re.sub(r"[^a-z0-9]+", "-", str(name if name is not None else "").lower())
    text = text.strip("-")
    if not text:
        raise ValidationError(f"Nombre de skill inválido: {name!r}")
    return validate_playbook_slug(text)


def _frontmatter(text: str) -> tuple[dict[str, str], str]:
    """Separa el frontmatter YAML simple del cuerpo de ``SKILL.md``.

    Solo interesan ``name`` y ``description`` de primer nivel; el bloque
    anidado (``metadata``) se ignora sin intentar interpretar YAML completo.
    """
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return {}, text
    closing = None
    for index in range(1, len(lines)):
        if lines[index].strip() == "---":
            closing = index
            break
    if closing is None:
        return {}, text
    meta: dict[str, str] = {}
    for line in lines[1:closing]:
        if not line or line[0] in (" ", "\t", "#"):
            continue
        key, separator, value = line.partition(":")
        if not separator:
            continue
        normalized = key.strip().lower()
        if normalized in _FRONTMATTER_KEYS:
            meta[normalized] = value.strip().strip("\"'")
    body = "\n".join(lines[closing + 1 :]).lstrip("\n")
    return meta, body


def _first_line(body: str) -> str:
    """Primera línea descriptiva del cuerpo, sin encabezados ni listas."""
    for line in body.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        text = stripped.lstrip(">*- ").strip()
        if text:
            return text
    return ""


def _description(meta: dict[str, str], body: str, name: str) -> str:
    text = meta.get("description") or _first_line(body) or f"Skill curada {name}"
    text = re.sub(r"\s+", " ", text).strip()
    if len(text) > PLAYBOOK_DESCRIPTION_CHARS:
        text = text[: PLAYBOOK_DESCRIPTION_CHARS - 3].rstrip() + "..."
    return text


def _support_path(path: str) -> str:
    """Normaliza una ruta de apoyo a los prefijos que admite la memoria.

    ``references/``, ``scripts/``, ``assets/`` y ``templates/`` se respetan.
    ``reference/`` (singular) y cualquier otro fichero documental se reubica
    bajo ``references/`` conservando su ruta relativa; nada sale del bundle y
    toda ruta pasa por ``validate_memory_path``.
    """
    text = str(path or "").strip()
    if not text:
        raise ValidationError("La skill declara un fichero de apoyo sin ruta")
    if "\x00" in text or "\n" in text or "\r" in text:
        raise ValidationError(f"Ruta de apoyo inválida: {text!r}")
    if "\\" in text:
        raise ValidationError(f"Ruta de apoyo inválida: {text!r} (usa '/')")
    if text.startswith(("/", "~")) or re.match(r"^[A-Za-z]:", text):
        raise ValidationError(f"Ruta de apoyo inválida: {text!r} (debe ser relativa)")
    normalized = posixpath.normpath(text)
    if normalized in (".", "..") or normalized.startswith("../"):
        raise ValidationError(
            f"Ruta de apoyo inválida: {text!r} (no puede salir del proyecto)"
        )
    if normalized != text:
        raise ValidationError(
            f"Ruta de apoyo inválida: {text!r} (normaliza a {normalized!r})"
        )
    if any(normalized.startswith(prefix) for prefix in ALLOWED_PATH_PREFIXES):
        return validate_memory_path(normalized)
    if normalized.startswith("reference/"):
        return validate_memory_path(
            "references/" + normalized[len("reference/") :]
        )
    return validate_memory_path("references/" + normalized)


def _support_files(bundle: dict[str, Any]) -> list[dict[str, str]]:
    """Convierte los ficheros del bundle (menos SKILL.md) en apoyos de memoria."""
    files: list[dict[str, str]] = []
    seen: set[str] = set()
    for item in bundle.get("files") or []:
        path = str(item.get("path") or "")
        if path == SKILL_FILE:
            continue
        target = _support_path(path)
        if target in seen:
            raise ValidationError(
                f"La skill {bundle.get('name')} produce una ruta de apoyo duplicada: "
                f"{target}"
            )
        seen.add(target)
        files.append({"path": target, "content": str(item.get("content") or "")})
    return files


def _skill_body(bundle: dict[str, Any]) -> tuple[dict[str, str], str]:
    for item in bundle.get("files") or []:
        if str(item.get("path")) == SKILL_FILE:
            meta, body = _frontmatter(str(item.get("content") or ""))
            if not body.strip():
                raise ValidationError(
                    f"La skill {bundle.get('name')} tiene un SKILL.md vacío"
                )
            return meta, body
    raise ValidationError(f"La skill {bundle.get('name')} no incluye {SKILL_FILE}")


def _live_playbook(conn: sqlite3.Connection, slug: str) -> sqlite3.Row | None:
    placeholders = ", ".join("?" for _ in LIVE_STATUSES)
    return conn.execute(
        "SELECT * FROM memories WHERE kind = ? AND \"key\" = ? "
        f"AND status IN ({placeholders}) ORDER BY id DESC LIMIT 1",
        (PLAYBOOK_KIND, playbook_key(slug), *LIVE_STATUSES),
    ).fetchone()


def install_skill(
    root: str | Path,
    conn: sqlite3.Connection,
    registry: dict[str, Any],
    name: str,
    fetch: Fetch | None = None,
    *,
    ref: str = "main",
    base_url: str = RAW_BASE_URL,
    actor: str | None = None,
    session_id: str | None = None,
    ticket_id: str | None = None,
    supersede: bool = False,
    config: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Descarga, verifica y guarda una skill como playbook ``proposed``.

    - Escanea el cuerpo y cada apoyo con ``memory.security.scan_content``.
    - No ejecuta scripts: solo los guarda como texto de referencia.
    - No sobrescribe un playbook vigente salvo con ``supersede=True``.
    """
    key = str(name or "").strip()
    slug = skill_slug(key)
    existing = _live_playbook(conn, slug)
    if existing is not None and not supersede:
        raise ValidationError(
            f"Ya existe el playbook {existing['code']} ({slug}); usa remove o "
            "supersede=True para sustituirlo con aprobación."
        )

    bundle = fetch_bundle(
        key, registry, fetch=fetch, ref=ref, base_url=base_url
    )
    meta, body = _skill_body(bundle)
    files = _support_files(bundle)

    cfg = memory_config(config)
    limit = int(cfg["max_playbook_chars"])
    scan_content(body, max_chars=limit, field=f"{key}/{SKILL_FILE}")
    for item in files:
        scan_content(
            item["content"],
            max_chars=limit,
            field=f"{key}/{item['path']}",
        )

    commit = str(bundle.get("commitSha") or "")
    source = str(bundle.get("source") or "")
    bundle_hash = str(bundle.get("bundleHash") or "")
    skill_url = bundle.get("skill_url") or skill_file_url(
        key, SKILL_FILE, ref=ref, base_url=base_url
    )
    reason = (
        f"AutoSkills {source} commit {commit[:12] or 'desconocido'} "
        f"bundle {bundle_hash[:12] or 'desconocido'}"
    )
    created = playbooks.create_playbook(
        conn,
        slug=slug,
        title=meta.get("name") or key,
        description=_description(meta, body, key),
        content=body,
        evidence=[
            {"kind": "url", "ref": skill_url},
            {"kind": "commit", "ref": commit},
        ],
        files=files,
        confidence=0.6,
        source="import",
        status="proposed",
        actor=actor,
        session_id=session_id,
        ticket_id=ticket_id,
        reason=reason,
        config=cfg,
    )
    result = dict(created)
    result["skill"] = {
        "name": key,
        "slug": slug,
        "source": source,
        "commitSha": commit,
        "bundleHash": bundle_hash,
        "url": skill_url,
        "path": str(
            Path(root).expanduser()
            / ".taiki"
            / "generated"
            / "skills"
            / slug
            / SKILL_FILE
        ),
        "files": len(files),
        "review": bundle.get("review"),
        "securityCheck": bundle.get("securityCheck"),
        "supersede": supersede,
    }
    return result


def list_skills(
    conn: sqlite3.Connection,
    *,
    include_inactive: bool = False,
    limit: int = 50,
) -> list[dict[str, Any]]:
    """Lista playbooks con su procedencia; por defecto oculta los archivados."""
    clause = (
        ""
        if include_inactive
        else "AND m.status NOT IN ('archived', 'superseded')"
    )
    rows = conn.execute(
        "SELECT m.id, m.code, m.title, m.description, m.status, m.source, m.\"key\", "
        "(SELECT e.ref FROM memory_evidence e "
        " WHERE e.memory_id = m.id AND e.kind = 'url' "
        " ORDER BY e.rowid LIMIT 1) AS url "
        "FROM memories m WHERE m.kind = ? "
        f"{clause} ORDER BY m.id DESC LIMIT ?",
        (PLAYBOOK_KIND, int(limit)),
    ).fetchall()
    out: list[dict[str, Any]] = []
    for row in rows:
        url = str(row["url"] or "")
        out.append(
            {
                "id": int(row["id"]),
                "code": row["code"],
                "name": parse_playbook_key(row["key"]),
                "title": row["title"],
                "description": row["description"],
                "status": row["status"],
                "source": row["source"],
                "origin": "autoskills" if "autoskills" in url else "local",
                "url": url or None,
            }
        )
    return out


def remove_skill(
    conn: sqlite3.Connection,
    name: object,
    *,
    actor: str | None = None,
    session_id: str | None = None,
    reason: str | None = None,
    config: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Archiva una skill instalada; el histórico queda en el ledger."""
    slug = skill_slug(name)
    return playbooks.archive_playbook(
        conn,
        slug,
        actor=actor,
        session_id=session_id,
        reason=reason or "Skill retirada del proyecto.",
        config=config,
    )


__all__ = [
    "SKILL_FILE",
    "install_skill",
    "list_skills",
    "remove_skill",
    "skill_slug",
]
