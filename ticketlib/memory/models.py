"""Constantes y normalizaciones de la memoria semántica."""

from __future__ import annotations

import re
import unicodedata
from typing import Iterable

from ..domain import ValidationError

MEMORY_SCOPES = ("project", "user")
MEMORY_KINDS = (
    "fact",
    "preference",
    "convention",
    "pitfall",
    "decision",
    "workflow",
    "reference",
)
MEMORY_STATUSES = ("proposed", "active", "stale", "archived", "superseded")
MEMORY_SOURCES = ("user", "agent", "derived", "import")
EVIDENCE_KINDS = ("ticket", "commit", "file", "url", "session")
USAGE_ACTIONS = ("brief", "search", "get", "feedback")
WRITE_POLICIES = ("balanced", "manual")

# Estados que consumen presupuesto. Una propuesta también ocupa sitio: si no,
# aprobarla podría romper el límite sin avisar.
BUDGET_STATUSES = ("active", "proposed", "stale")
# Estados que participan en dedupe y supersesión por clave.
LIVE_STATUSES = ("active", "proposed", "stale")

MEMORY_COUNTER = "next_memory_number"
MEMORY_CODE_RE = re.compile(r"^M-\d{4,}$")
TICKET_CODE_RE = re.compile(r"^[TS]-\d+(?:\.\d+)?$")

NEGATIVE_RULE_RE = re.compile(
    r"\b(nunca|prohibido|no\s+uses|no\s+usar|never|do\s+not\s+use|don't\s+use)\b",
    re.IGNORECASE,
)


def normalize_fingerprint(*parts: object) -> str:
    """Huella normalizada: sin acentos, minúsculas, sin puntuación.

    Los espacios se colapsan para que dos textos equivalentes con distinto
    formato produzcan la misma huella y el dedupe no dependa de la superficie.
    """
    text = " ".join(str(part or "") for part in parts)
    ascii_text = (
        unicodedata.normalize("NFKD", text)
        .encode("ascii", "ignore")
        .decode("ascii")
        .lower()
    )
    without_punctuation = re.sub(r"[^a-z0-9\s]+", " ", ascii_text)
    return re.sub(r"\s+", " ", without_punctuation).strip()


def default_key(title: str) -> str:
    """Clave estable a partir del título cuando no se indica una."""
    return normalize_fingerprint(title).replace(" ", "-")[:80] or "memoria"


def is_negative_rule(*parts: object) -> bool:
    """¿El texto expresa una prohibición amplia que requiere aprobación?"""
    text = " ".join(str(part or "") for part in parts)
    return bool(NEGATIVE_RULE_RE.search(text))


def clean_evidence_kind(value: object) -> str:
    """Valida el tipo de evidencia."""
    kind = str(value or "").strip().lower()
    if kind not in EVIDENCE_KINDS:
        raise ValidationError(
            f"Tipo de evidencia inválido: {kind!r}. "
            f"Valores permitidos: {', '.join(EVIDENCE_KINDS)}"
        )
    return kind


def evidence_kind_for_ref(ref: str) -> str:
    """Infiere el tipo de evidencia a partir de una referencia suelta."""
    text = ref.strip()
    if TICKET_CODE_RE.fullmatch(text.upper()):
        return "ticket"
    if text.startswith(("http://", "https://")):
        return "url"
    if text.lower().startswith("ses_"):
        return "session"
    if re.fullmatch(r"[0-9a-f]{7,40}", text):
        return "commit"
    return "file"


def iter_statuses(values: Iterable[str] | None) -> tuple[str, ...]:
    """Normaliza una selección de estados."""
    if not values:
        return MEMORY_STATUSES
    selected = tuple(dict.fromkeys(str(value) for value in values))
    for value in selected:
        if value not in MEMORY_STATUSES:
            raise ValidationError(
                f"Estado de memoria inválido: {value!r}. "
                f"Valores permitidos: {', '.join(MEMORY_STATUSES)}"
            )
    return selected
