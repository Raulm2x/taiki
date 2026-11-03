"""Helpers compartidos del almacén de memoria (filas, validación y recibos)."""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from pathlib import Path
from typing import Any, Iterable

from .. import db
from ..domain import KINDS, ValidationError
from ..gitutil import resolve_commit
from ..util import check_enum, now
from . import budget as budget_lib
from . import ledger as ledger_lib
from . import security
from .config import resolve_memory_config
from .models import (
    BUDGET_STATUSES,
    EVIDENCE_KINDS,
    MEMORY_CODE_RE,
    MEMORY_COUNTER,
    MEMORY_KINDS,
    MEMORY_SCOPES,
    MEMORY_SOURCES,
    MEMORY_STATUSES,
    TICKET_CODE_RE,
    default_key,
    evidence_kind_for_ref,
    is_negative_rule,
    normalize_fingerprint,
)


def config(value: dict[str, Any] | None) -> dict[str, Any]:
    """Resuelve el config raíz, el bloque memory o los defaults."""
    return resolve_memory_config(value)


def load_list(value: Any) -> list[str]:
    """Convierte una columna JSON de lista en una lista de strings."""
    if value is None or value == "":
        return []
    if isinstance(value, list):
        return [str(item) for item in value]
    try:
        parsed = json.loads(str(value))
    except json.JSONDecodeError:
        return [str(value)]
    if isinstance(parsed, list):
        return [str(item) for item in parsed]
    return [str(parsed)]


def row_to_memory(
    row: sqlite3.Row,
    conn: sqlite3.Connection | None = None,
    *,
    details: bool = False,
) -> dict[str, Any]:
    """Convierte una fila en dict con listas y booleanos normalizados."""
    data = dict(row)
    data["pinned"] = bool(data.get("pinned"))
    data["locked"] = bool(data.get("locked"))
    data["paths"] = load_list(data.get("paths"))
    data["kinds"] = load_list(data.get("kinds"))
    data["ticket_kinds"] = load_list(data.get("ticket_kinds"))
    if details and conn is not None:
        data["evidence"] = [
            dict(item)
            for item in conn.execute(
                "SELECT kind, ref, created_at FROM memory_evidence "
                "WHERE memory_id = ? ORDER BY kind, ref",
                (data["id"],),
            )
        ]
        data["files"] = [
            dict(item)
            for item in conn.execute(
                "SELECT path, sha256 FROM memory_files "
                "WHERE memory_id = ? ORDER BY path",
                (data["id"],),
            )
        ]
    return data


def memory_row(conn: sqlite3.Connection, ref: Any) -> sqlite3.Row:
    """Resuelve código M-#### o id y devuelve la fila."""
    if isinstance(ref, bool):
        raise ValidationError(f"Memoria inválida: {ref!r}")
    if isinstance(ref, int):
        row = conn.execute("SELECT * FROM memories WHERE id = ?", (ref,)).fetchone()
    else:
        text = str(ref or "").strip().upper()
        match = re.fullmatch(r"M-(\d+)", text)
        if match:
            text = f"M-{int(match.group(1)):04d}"
        if not MEMORY_CODE_RE.fullmatch(text):
            raise ValidationError(
                f"Código de memoria inválido: {ref!r} (esperado M-0001)"
            )
        row = conn.execute("SELECT * FROM memories WHERE code = ?", (text,)).fetchone()
    if row is None:
        raise ValidationError(f"Memoria no encontrada: {ref}")
    return row


def allocate_code(conn: sqlite3.Connection) -> str:
    """Reserva el siguiente código M-#### en meta."""
    number = int(db.meta_get(conn, MEMORY_COUNTER, "1") or "1")
    db.meta_set(conn, MEMORY_COUNTER, str(number + 1))
    return f"M-{number:04d}"


def clean_text(
    value: object,
    field: str,
    max_chars: int,
    *,
    required: bool = True,
) -> str:
    """Limpia y escanea un texto de memoria."""
    text = str(value if value is not None else "").strip()
    if required and not text:
        raise ValidationError(f"El campo {field} de la memoria es obligatorio")
    security.scan_content(text, max_chars=max_chars, field=field)
    return text


def clean_short(
    value: object,
    field: str,
    *,
    max_chars: int = 500,
    required: bool = False,
) -> str:
    """Valida metadatos cortos (actor, sesión, clave, evidencia o nota)."""
    text = str(value if value is not None else "").strip()
    if required and not text:
        raise ValidationError(f"El campo {field} es obligatorio")
    security.scan_content(text, max_chars=max_chars, field=field)
    return text


def clean_key(value: object, *, max_chars: int = 200) -> str:
    """Valida una clave de memoria."""
    return clean_short(value, "clave", max_chars=max_chars, required=True)


def confidence(value: object) -> float:
    """Valida una confianza entre 0 y 1."""
    if isinstance(value, bool):
        raise ValidationError("La confianza debe ser un número entre 0 y 1")
    try:
        number = float(str(value))
    except (TypeError, ValueError) as exc:
        raise ValidationError(
            "La confianza debe ser un número entre 0 y 1"
        ) from exc
    if not 0.0 <= number <= 1.0:
        raise ValidationError("La confianza debe estar entre 0 y 1")
    return number


def ticket_id(value: object) -> str | None:
    """Normaliza un ticket_id opcional."""
    if value is None or str(value).strip() == "":
        return None
    text = str(value).strip().upper()
    if not TICKET_CODE_RE.fullmatch(text):
        raise ValidationError(
            f"ticket_id inválido: {value!r} (esperado T-0001 o T-0001.01)"
        )
    return text


def clean_project_paths(paths: Iterable[object] | None) -> list[str]:
    """Rutas de proyecto: relativas, sin ``..`` y sin duplicados."""
    if paths is None:
        return []
    if isinstance(paths, str):
        paths = [paths]
    out: list[str] = []
    for item in paths:
        text = clean_short(item, "ruta de memoria", max_chars=500).replace("\\", "/")
        if not text or text.startswith(("/", "~")) or re.match(r"^[A-Za-z]:", text):
            raise ValidationError(
                f"Ruta de memoria inválida: {item!r} (debe ser relativa)"
            )
        if ".." in text.split("/"):
            raise ValidationError(
                f"Ruta de memoria inválida: {item!r} (no puede salir del proyecto)"
            )
        if text not in out:
            out.append(text)
    return out


def clean_kinds(kinds: Iterable[object] | None) -> list[str]:
    """Valida la lista de kinds secundarios."""
    if kinds is None:
        return []
    if isinstance(kinds, str):
        kinds = [kinds]
    out: list[str] = []
    for item in kinds:
        value = str(item or "").strip()
        if value and value not in MEMORY_KINDS:
            raise ValidationError(
                f"kind de memoria inválido: {value!r}. "
                f"Valores permitidos: {', '.join(MEMORY_KINDS)}"
            )
        if value and value not in out:
            out.append(value)
    return out


def clean_ticket_kinds(kinds: Iterable[object] | None) -> list[str]:
    """Valida las etiquetas de tipo de ticket (vacío = general)."""
    if kinds is None:
        return []
    if isinstance(kinds, str):
        kinds = [kinds]
    out: list[str] = []
    for item in kinds:
        value = str(item or "").strip()
        if value and value not in KINDS:
            raise ValidationError(
                f"tipo de ticket inválido: {value!r}. "
                f"Valores permitidos: {', '.join(KINDS)}"
            )
        if value and value not in out:
            out.append(value)
    return out


def clean_evidence(evidence: Iterable[Any] | None) -> list[dict[str, str]]:
    """Normaliza y valida evidencias; infiere el tipo si falta."""
    if evidence is None:
        return []
    if isinstance(evidence, (dict, str, tuple)):
        evidence = [evidence]
    out: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for item in evidence:
        kind: Any = None
        ref: Any = None
        if isinstance(item, dict):
            kind = item.get("kind")
            ref = item.get("ref") or item.get("value")
        elif isinstance(item, (list, tuple)) and len(item) >= 2:
            kind, ref = item[0], item[1]
        elif isinstance(item, str):
            ref = item
        else:
            raise ValidationError(f"Evidencia inválida: {item!r}")
        reference = clean_short(
            ref, f"evidencia {str(kind or 'auto')}", max_chars=500, required=True
        )
        evidence_kind = str(kind or evidence_kind_for_ref(reference)).strip().lower()
        if evidence_kind not in EVIDENCE_KINDS:
            raise ValidationError(
                f"Tipo de evidencia inválido: {evidence_kind!r}. "
                f"Valores permitidos: {', '.join(EVIDENCE_KINDS)}"
            )
        if evidence_kind == "ticket":
            reference = reference.upper()
            if not TICKET_CODE_RE.fullmatch(reference):
                raise ValidationError(f"Evidencia de ticket inválida: {reference!r}")
        pair = (evidence_kind, reference)
        if pair in seen:
            continue
        seen.add(pair)
        out.append({"kind": evidence_kind, "ref": reference})
    return out


def clean_files(
    files: Iterable[Any] | None,
    cfg: dict[str, Any],
) -> list[dict[str, str]]:
    """Valida los ficheros de referencia y calcula su sha256."""
    if files is None:
        return []
    if isinstance(files, (dict, tuple)):
        files = [files]
    out: dict[str, dict[str, str]] = {}
    for item in files:
        if isinstance(item, dict):
            path = item.get("path")
            content = item.get("content", "")
        elif isinstance(item, (list, tuple)) and len(item) >= 2:
            path, content = item[0], item[1]
        else:
            raise ValidationError(f"Fichero de memoria inválido: {item!r}")
        normalized = security.validate_memory_path(path)
        text = security.scan_content(
            content,
            max_chars=int(cfg["max_playbook_chars"]),
            field=f"fichero {normalized}",
        )
        out[normalized] = {
            "path": normalized,
            "content": text,
            "sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
        }
    return list(out.values())


def validated_evidence(
    conn: sqlite3.Connection,
    evidence: Iterable[dict[str, str]] | None,
    root: str | Path | None = None,
) -> list[dict[str, str]]:
    """Devuelve solo las evidencias comprobables que siguen vigentes.

    ``ticket`` exige un ticket cerrado (verificado o confirmado), ``commit`` un
    objeto Git resoluble y ``file`` una ruta existente. ``url`` y ``session`` no
    se pueden validar offline, así que nunca cuentan.
    """
    out: list[dict[str, str]] = []
    for item in evidence or []:
        kind = str(item.get("kind") or "")
        ref = str(item.get("ref") or "").strip()
        if not ref:
            continue
        valid = False
        if kind == "ticket":
            row = conn.execute(
                "SELECT status FROM tickets WHERE code = ?", (ref.upper(),)
            ).fetchone()
            valid = row is not None and row["status"] in ("verificado", "confirmado")
        elif kind == "commit":
            valid = resolve_commit(ref, root) is not None
        elif kind == "file":
            valid = Path(ref).expanduser().exists()
        if valid:
            out.append({"kind": kind, "ref": ref})
    return out


def decide_status(
    *,
    kind: str,
    source: str,
    evidence_count: int,
    confidence_value: float,
    text: str,
    policy: str,
    validated_evidence: int = 0,
    auto_approve: bool = True,
) -> str:
    """Política balanced: lo automático nace propuesto salvo evidencia sólida."""
    if policy == "manual":
        return "proposed"
    if is_negative_rule(text):
        return "proposed"
    if source in ("user", "derived", "import"):
        return "active"
    if (
        source == "agent"
        and kind in ("fact", "convention", "pitfall")
        and auto_approve
        and validated_evidence >= 1
    ):
        return "active"
    if (
        source == "agent"
        and kind in ("fact", "pitfall")
        and evidence_count >= 2
        and confidence_value >= 0.7
    ):
        return "active"
    return "proposed"


def scope_entries(
    conn: sqlite3.Connection,
    scope: str,
) -> list[dict[str, Any]]:
    """Entradas del scope que consumen presupuesto."""
    rows = conn.execute(
        "SELECT * FROM memories WHERE scope = ? AND status IN (?, ?, ?)",
        (scope, *BUDGET_STATUSES),
    ).fetchall()
    return [dict(row) for row in rows]


def limit_for(cfg: dict[str, Any], scope: str) -> int:
    """Límite de caracteres del scope."""
    key = "user_budget_chars" if scope == "user" else "project_budget_chars"
    return int(cfg[key])


def check_budget(
    conn: sqlite3.Connection,
    scope: str,
    cfg: dict[str, Any],
) -> dict[str, Any]:
    """Comprueba el presupuesto sobre el batch ya escrito.

    Con ``enforce_budget`` desactivado solo devuelve el resumen: el brief
    selecciona por prioridad y un exceso es un aviso, no un bloqueo.
    """
    entries = scope_entries(conn, scope)
    limit = limit_for(cfg, scope)
    if not cfg["enforce_budget"]:
        return budget_lib.summary(entries, limit, scope)
    return budget_lib.enforce(entries, limit, scope)


def receipt(
    conn: sqlite3.Connection,
    row: sqlite3.Row,
    cfg: dict[str, Any],
) -> dict[str, Any]:
    """Recibo de mutación con estado, presupuesto y avisos."""
    data = row_to_memory(row, conn, details=True)
    entries = scope_entries(conn, data["scope"])
    data["budget"] = budget_lib.summary(
        entries, limit_for(cfg, data["scope"]), data["scope"]
    )
    warnings: list[str] = []
    budget = data["budget"]
    budget_warning = None
    if budget["used"] > budget["limit"]:
        budget_warning = (
            f"Presupuesto de memoria '{budget['scope']}' superado: "
            f"{budget['used']}/{budget['limit']}; "
            "el brief selecciona por prioridad"
        )
        warnings.append(budget_warning)
    data["budget_warning"] = budget_warning
    if data["status"] == "proposed":
        warnings.append("Pendiente de aprobación humana antes de usarse en el brief")
    if data.get("superseded_by"):
        warnings.append(f"Sustituida por {data['superseded_by']}")
    data["warnings"] = warnings
    return data


def insert_evidence(
    conn: sqlite3.Connection,
    memory_id: int,
    evidence: list[dict[str, str]],
) -> None:
    """Inserta evidencias ignorando duplicados."""
    timestamp = now()
    for item in evidence:
        conn.execute(
            "INSERT OR IGNORE INTO memory_evidence(memory_id, kind, ref, created_at) "
            "VALUES (?, ?, ?, ?)",
            (memory_id, item["kind"], item["ref"], timestamp),
        )


def insert_files(
    conn: sqlite3.Connection,
    memory_id: int,
    files: list[dict[str, str]],
) -> None:
    """Guarda ficheros de referencia y sus blobs content-addressed."""
    for item in files:
        ledger_lib.store_blob(conn, item["content"])
        conn.execute(
            "INSERT OR REPLACE INTO memory_files(memory_id, path, content, sha256) "
            "VALUES (?, ?, ?, ?)",
            (memory_id, item["path"], item["content"], item["sha256"]),
        )


def record_usage(
    conn: sqlite3.Connection,
    memory_id: int,
    action: str,
    session_id: str | None = None,
    *,
    helpful: bool | None = None,
    note: str | None = None,
    touch: bool = True,
) -> None:
    """Registra uso y, si procede, incrementa el contador de la memoria."""
    timestamp = now()
    safe_session = (
        clean_short(session_id, "session_id", max_chars=200)
        if session_id is not None
        else None
    )
    safe_note = (
        clean_short(note, "nota de uso", max_chars=1000)
        if note is not None
        else None
    )
    conn.execute(
        "INSERT INTO memory_usage(ts, memory_id, session_id, action, helpful, note) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (
            timestamp,
            memory_id,
            safe_session,
            action,
            None if helpful is None else int(helpful),
            safe_note,
        ),
    )
    if touch and action in ("brief", "search", "get"):
        conn.execute(
            "UPDATE memories SET use_count = use_count + 1, last_used_at = ? "
            "WHERE id = ?",
            (timestamp, memory_id),
        )


__all__ = [
    "allocate_code",
    "check_budget",
    "clean_evidence",
    "clean_files",
    "clean_key",
    "clean_kinds",
    "clean_project_paths",
    "clean_short",
    "clean_text",
    "clean_ticket_kinds",
    "config",
    "confidence",
    "decide_status",
    "insert_evidence",
    "insert_files",
    "limit_for",
    "load_list",
    "memory_row",
    "receipt",
    "record_usage",
    "row_to_memory",
    "scope_entries",
    "ticket_id",
    "validated_evidence",
]
