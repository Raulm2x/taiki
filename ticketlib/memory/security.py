"""Escaneo de contenido y validación de rutas de la memoria."""

from __future__ import annotations

import posixpath
import re
from typing import Iterable

from ..domain import ValidationError

# Patrones de secretos habituales. El mensaje nunca incluye el valor
# encontrado: solo el nombre del patrón y la posición aproximada.
SECRET_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("clave OpenAI", re.compile(r"\bsk-[A-Za-z0-9_-]{16,}\b")),
    ("token GitHub", re.compile(r"\bghp_[A-Za-z0-9]{20,}\b")),
    ("clave AWS", re.compile(r"\bAKIA[0-9A-Z]{16}\b")),
    ("clave privada", re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----")),
    (
        "credencial en claro",
        re.compile(
            r"(?i)\b(password|passwd|contrasena|contraseña|token|secret|api[_-]?key)\b"
            r"\s*[:=]\s*\S+"
        ),
    ),
)

INJECTION_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    (
        "inyección de prompt",
        re.compile(
            r"(?i)\b(ignore|disregard)\s+(all\s+)?(the\s+)?previous\s+instructions?\b"
        ),
    ),
    ("inyección de prompt", re.compile(r"(?i)\bsystem\s+prompt\b")),
    ("inyección de prompt", re.compile(r"(?i)\byou\s+are\s+now\b")),
    (
        "inyección de prompt",
        re.compile(r"(?i)\b(ignora|olvida)\s+(todas\s+)?las\s+instrucciones\b"),
    ),
    ("inyección de prompt", re.compile(r"(?i)\bahora\s+eres\b")),
)

INVISIBLE_RE = re.compile(
    "[\u200b\u200c\u200d\u200e\u200f\u2060\ufeff\u202a-\u202e\u2066-\u2069]"
)
CONTROL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f]")

ALLOWED_PATH_PREFIXES = ("references/", "scripts/", "assets/", "templates/")


def scan_content(
    text: object,
    *,
    max_chars: int,
    field: str = "contenido",
) -> str:
    """Valida un texto antes de guardarlo en memoria.

    Rechaza secretos, intentos de inyección de prompt, Unicode invisible y
    contenido por encima del límite. Devuelve el texto original para encadenar
    la validación con la escritura.
    """
    value = str(text if text is not None else "")
    if len(value) > max_chars:
        raise ValidationError(
            f"{field} demasiado largo: {len(value)} caracteres "
            f"(máximo {max_chars}). Resume la memoria antes de guardarla."
        )

    for name, pattern in SECRET_PATTERNS:
        if pattern.search(value):
            raise ValidationError(
                f"{field} parece contener un secreto ({name}); "
                "no se guarda. Usa una referencia, no la credencial."
            )

    for name, pattern in INJECTION_PATTERNS:
        if pattern.search(value):
            raise ValidationError(
                f"{field} contiene una posible {name}; "
                "reformula el texto como una nota neutral."
            )

    invisible = INVISIBLE_RE.search(value)
    if invisible:
        codepoint = f"U+{ord(invisible.group(0)):04X}"
        raise ValidationError(
            f"{field} contiene Unicode invisible ({codepoint}); "
            "elimina los caracteres de control de dirección o ancho cero."
        )

    control = CONTROL_RE.search(value)
    if control:
        codepoint = f"U+{ord(control.group(0)):04X}"
        raise ValidationError(
            f"{field} contiene un carácter de control ({codepoint}) no permitido."
        )
    return value


def validate_memory_path(path: object) -> str:
    """Valida una ruta de ``memory_files`` y la devuelve normalizada.

    Solo se admiten rutas relativas dentro de ``references/``, ``scripts/``,
    ``assets/`` o ``templates/``. Los absolutos, ``..`` y los enlaces
    simbólicos encubiertos por rutas raras se rechazan antes de escribir.
    """
    text = str(path or "").strip()
    if not text:
        raise ValidationError("La ruta de un fichero de memoria es obligatoria")
    if "\x00" in text or "\n" in text or "\r" in text:
        raise ValidationError(f"Ruta de memoria inválida: {text!r}")
    if "\\" in text:
        raise ValidationError(
            f"Ruta de memoria inválida: {text!r} (usa separadores '/')"
        )
    if text.startswith(("/", "~")) or re.match(r"^[A-Za-z]:", text):
        raise ValidationError(
            f"Ruta de memoria inválida: {text!r} (debe ser relativa)"
        )

    normalized = posixpath.normpath(text)
    if normalized in (".", "..") or normalized.startswith("../"):
        raise ValidationError(
            f"Ruta de memoria inválida: {text!r} (no puede salir del proyecto)"
        )
    if normalized != text.rstrip("/"):
        raise ValidationError(
            f"Ruta de memoria inválida: {text!r} (normaliza a {normalized!r})"
        )
    if not any(normalized.startswith(prefix) for prefix in ALLOWED_PATH_PREFIXES):
        raise ValidationError(
            f"Ruta de memoria no permitida: {text!r}. "
            f"Debe empezar por {', '.join(ALLOWED_PATH_PREFIXES)}"
        )
    if normalized.endswith("/") or normalized in ALLOWED_PATH_PREFIXES:
        raise ValidationError(
            f"Ruta de memoria inválida: {text!r} (falta el nombre del fichero)"
        )
    return normalized


def validate_memory_paths(paths: Iterable[object] | None) -> list[str]:
    """Valida y deduplica una lista de rutas de memoria."""
    if paths is None:
        return []
    if isinstance(paths, str):
        paths = [paths]
    out: list[str] = []
    for item in paths:
        value = validate_memory_path(item)
        if value not in out:
            out.append(value)
    return out


__all__ = [
    "ALLOWED_PATH_PREFIXES",
    "scan_content",
    "validate_memory_path",
    "validate_memory_paths",
]
