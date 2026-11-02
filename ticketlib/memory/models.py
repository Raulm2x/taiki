"""Constantes y normalizaciones de la memoria semántica."""

from __future__ import annotations

import hashlib
import re
import unicodedata

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
    """Huella normalizada y consciente de Unicode.

    Conserva letras y números de cualquier alfabeto (CJK, cirílico, etc.),
    elimina acentos descompuestos, puntuación y símbolos, y colapsa espacios.
    Si el texto solo contiene símbolos (por ejemplo emojis), usa un hash
    estable para no colapsar todos los casos a la cadena vacía.
    """
    text = " ".join(str(part or "") for part in parts)
    decomposed = unicodedata.normalize("NFKD", text).casefold()
    without_marks = "".join(
        char
        for char in decomposed
        if unicodedata.category(char) not in ("Mn", "Mc", "Me")
    )
    kept = "".join(
        char if (char.isalnum() or char.isspace()) else " "
        for char in without_marks
    )
    collapsed = re.sub(r"\s+", " ", kept).strip()
    if collapsed:
        return collapsed
    return "sha256:" + hashlib.sha256(decomposed.encode("utf-8")).hexdigest()


def default_key(title: str) -> str:
    """Clave estable a partir del título cuando no se indica una."""
    return normalize_fingerprint(title).replace(" ", "-")[:80] or "memoria"


def is_negative_rule(*parts: object) -> bool:
    """¿El texto expresa una prohibición amplia que requiere aprobación?"""
    text = " ".join(str(part or "") for part in parts)
    return bool(NEGATIVE_RULE_RE.search(text))


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

