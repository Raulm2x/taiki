"""Registro de skills de AutoSkills: carga, validación y descarga verificada.

El registro es un JSON con ``skills`` indexado por nombre; cada entrada declara
``source``, ``commitSha``, ``files``, ``sha256`` por fichero y un ``bundleHash``
que se recalcula como SHA-256 de las líneas ``"<fichero>:<sha>"`` ordenadas y
unidas por saltos de línea (mismo algoritmo que valida el repositorio origen).

La red solo se toca en ``DEFAULT_FETCH``: los tests y las capas superiores
inyectan ``fetch`` para trabajar con fixtures.
"""

from __future__ import annotations

import hashlib
import json
import posixpath
import re
import time
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Callable

from ..domain import ValidationError

DEFAULT_REGISTRY_URL = (
    "https://raw.githubusercontent.com/midudev/autoskills/main/"
    "packages/autoskills/skills-registry/index.json"
)
RAW_BASE_URL = (
    "https://raw.githubusercontent.com/midudev/autoskills/main/"
    "packages/autoskills/skills-registry"
)
CACHE_RELATIVE = Path(".taiki") / "generated" / "skills" / ".cache"
CACHE_FILE = "registry.json"
DEFAULT_TTL_SECONDS = 24 * 60 * 60
MAX_DOWNLOAD_BYTES = 2 * 1024 * 1024
FETCH_TIMEOUT_SECONDS = 20

# Un ``fetch`` recibe una URL y devuelve los bytes (o texto) descargados.
Fetch = Callable[[str], bytes | str]

_HEX64 = re.compile(r"^[0-9a-f]{64}$")
_SHA = re.compile(r"^[0-9a-f]{7,40}$")
# El registro de AutoSkills publica nombres camelCase (p. ej. ``emailAndPassword``):
# se aceptan letras, dígitos, punto, guion y guion bajo, sin separadores de ruta.
_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")


def default_fetch(url: str) -> bytes:
    """Descarga una URL con ``urllib`` y un límite de tamaño.

    Es el único punto del núcleo que usa la red; nunca se invoca desde los
    tests ni desde la detección de stack.
    """
    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": "taiki-autoskills/1",
            "Accept": "application/json, text/plain, */*",
        },
    )
    with urllib.request.urlopen(  # noqa: S310 - URL del registro oficial
        request, timeout=FETCH_TIMEOUT_SECONDS
    ) as response:
        data = response.read(MAX_DOWNLOAD_BYTES + 1)
    if len(data) > MAX_DOWNLOAD_BYTES:
        raise ValidationError(f"Respuesta demasiado grande: {url}")
    return data


def _bytes(value: bytes | str) -> bytes:
    """Normaliza la salida de un ``fetch`` inyectado a bytes."""
    if isinstance(value, bytes):
        return value
    if isinstance(value, str):
        return value.encode("utf-8")
    raise ValidationError(f"fetch devolvió un tipo no válido: {type(value).__name__}")


def _safe_relative(path: object) -> str:
    """Valida una ruta relativa del bundle (sin absolutos ni traversal)."""
    text = str(path if path is not None else "").strip()
    if not text:
        raise ValidationError("El registro declara un fichero sin ruta")
    if "\x00" in text or "\n" in text or "\r" in text:
        raise ValidationError(f"Ruta de skill inválida: {text!r}")
    if "\\" in text:
        raise ValidationError(
            f"Ruta de skill inválida: {text!r} (usa separadores '/')"
        )
    if text.startswith(("/", "~")) or re.match(r"^[A-Za-z]:", text):
        raise ValidationError(f"Ruta de skill inválida: {text!r} (debe ser relativa)")
    normalized = posixpath.normpath(text)
    if normalized in (".", "..") or normalized.startswith("../"):
        raise ValidationError(
            f"Ruta de skill inválida: {text!r} (no puede salir del bundle)"
        )
    if normalized != text:
        raise ValidationError(
            f"Ruta de skill inválida: {text!r} (normaliza a {normalized!r})"
        )
    if not normalized or normalized.endswith("/"):
        raise ValidationError(f"Ruta de skill inválida: {text!r} (falta el fichero)")
    return normalized


def _validate_review(value: Any, field: str) -> dict[str, Any] | None:
    """Valida el bloque opcional ``review`` o ``securityCheck``."""
    if value is None:
        return None
    if not isinstance(value, dict):
        raise ValidationError(f"{field} debe ser un objeto")
    status = value.get("status")
    if status is not None and not isinstance(status, str):
        raise ValidationError(f"{field}.status debe ser texto")
    flags = value.get("flags", [])
    if flags is None:
        flags = []
    if not isinstance(flags, list) or any(not isinstance(item, str) for item in flags):
        raise ValidationError(f"{field}.flags debe ser una lista de textos")
    findings = value.get("findings", [])
    if findings is None:
        findings = []
    if not isinstance(findings, list) or any(
        not isinstance(item, str) for item in findings
    ):
        raise ValidationError(f"{field}.findings debe ser una lista de textos")
    return value


def validate_registry(data: Any) -> dict[str, Any]:
    """Valida el índice y devuelve una copia normalizada.

    Rechaza registros sin ``skills``, entradas incompletas, hashes que no sean
    SHA-256 y rutas de fichero inseguras o comprimidas.
    """
    if not isinstance(data, dict):
        raise ValidationError("El registro de skills debe ser un objeto JSON")
    version = data.get("version")
    if not isinstance(version, int) or isinstance(version, bool) or version < 1:
        raise ValidationError("El registro de skills no declara una versión válida")
    raw_skills = data.get("skills")
    if not isinstance(raw_skills, dict) or not raw_skills:
        raise ValidationError("El registro de skills está vacío o mal formado")

    skills: dict[str, dict[str, Any]] = {}
    for raw_name, raw_entry in raw_skills.items():
        name = str(raw_name)
        if not _NAME.match(name) or len(name) > 100:
            raise ValidationError(f"Nombre de skill inválido: {name!r}")
        if not isinstance(raw_entry, dict):
            raise ValidationError(f"La skill {name} no es un objeto")
        entry = validate_entry(name, raw_entry)
        skills[name] = entry

    normalized = dict(data)
    normalized["skills"] = skills
    return normalized


def validate_entry(name: str, raw: dict[str, Any]) -> dict[str, Any]:
    """Valida y normaliza la entrada de una skill concreta."""
    entry = dict(raw)
    source = entry.get("source")
    if not isinstance(source, str) or not source.strip() or len(source) > 200:
        raise ValidationError(f"La skill {name} no declara source válido")
    skill_path = entry.get("skillPath")
    if not isinstance(skill_path, str) or not skill_path.strip():
        raise ValidationError(f"La skill {name} no declara skillPath")
    commit = entry.get("commitSha")
    if not isinstance(commit, str) or not _SHA.fullmatch(commit):
        raise ValidationError(
            f"La skill {name} no declara un commitSha hexadecimal válido"
        )

    files = entry.get("files")
    if not isinstance(files, list) or not files:
        raise ValidationError(f"La skill {name} no declara ficheros")
    clean_files: list[str] = []
    for item in files:
        relative = _safe_relative(item)
        if relative.lower().endswith(".zip"):
            raise ValidationError(
                f"La skill {name} incluye un .zip no permitido: {relative}"
            )
        if relative in clean_files:
            raise ValidationError(f"La skill {name} repite el fichero {relative}")
        clean_files.append(relative)

    hashes = entry.get("sha256")
    if not isinstance(hashes, dict):
        raise ValidationError(f"La skill {name} no declara sha256 por fichero")
    if set(hashes) != set(clean_files):
        raise ValidationError(
            f"La skill {name} no tiene sha256 para exactamente sus ficheros"
        )
    clean_hashes: dict[str, str] = {}
    for relative in clean_files:
        digest = str(hashes.get(relative) or "").lower()
        if not _HEX64.fullmatch(digest):
            raise ValidationError(
                f"La skill {name} declara un sha256 inválido para {relative}"
            )
        clean_hashes[relative] = digest

    bundle = str(entry.get("bundleHash") or "").lower()
    if not _HEX64.fullmatch(bundle):
        raise ValidationError(f"La skill {name} declara un bundleHash inválido")

    entry["source"] = source.strip()
    entry["skillPath"] = skill_path.strip()
    entry["commitSha"] = commit.lower()
    entry["files"] = clean_files
    entry["sha256"] = clean_hashes
    entry["bundleHash"] = bundle
    entry["review"] = _validate_review(entry.get("review"), f"{name}.review")
    entry["securityCheck"] = _validate_review(
        entry.get("securityCheck"), f"{name}.securityCheck"
    )
    return entry


def bundle_hash(hashes: dict[str, str]) -> str:
    """Calcula el ``bundleHash`` oficial a partir de ``fichero -> sha256``."""
    parts = [f"{path}:{digest}" for path, digest in sorted(hashes.items())]
    return hashlib.sha256("\n".join(parts).encode("utf-8")).hexdigest()


def registry_skills(registry: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """Devuelve el mapa de skills de un registro ya validado."""
    skills = registry.get("skills")
    if not isinstance(skills, dict):
        raise ValidationError("El registro de skills no contiene 'skills'")
    return skills


def registry_entry(registry: dict[str, Any], name: str) -> dict[str, Any]:
    """Resuelve la entrada de una skill por nombre."""
    key = str(name or "").strip()
    entry = registry_skills(registry).get(key)
    if not isinstance(entry, dict):
        raise ValidationError(f"Skill desconocida en el registro: {key!r}")
    return entry


def _cache_path(
    root: str | Path | None, cache_dir: str | Path | None
) -> Path | None:
    if cache_dir is not None:
        return Path(cache_dir).expanduser() / CACHE_FILE
    if root is not None:
        return Path(root).expanduser() / CACHE_RELATIVE / CACHE_FILE
    return None


def _read_cache(path: Path) -> dict[str, Any] | None:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def _write_cache(path: Path, payload: dict[str, Any]) -> None:
    """Escribe la caché de forma atómica; un fallo de disco no rompe la carga."""
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        temp = path.with_name(f".{path.name}.tmp")
        temp.write_text(
            json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
            encoding="utf-8",
        )
        temp.replace(path)
    except OSError:
        return


def _parse_registry(raw: bytes, source: str) -> dict[str, Any]:
    try:
        data = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as exc:
        raise ValidationError(
            f"El registro de skills no es JSON UTF-8 válido ({source}): {exc}"
        ) from exc
    return validate_registry(data)


def load_registry(
    source: str | Path | None = None,
    *,
    root: str | Path | None = None,
    cache_dir: str | Path | None = None,
    ttl_seconds: int = DEFAULT_TTL_SECONDS,
    fetch: Fetch | None = None,
    now: Callable[[], float] | None = None,
    refresh: bool = False,
) -> dict[str, Any]:
    """Carga el registro desde un JSON local, una URL o la caché.

    - Un origen local se lee siempre del disco (no se cachea).
    - Una URL usa ``<root>/.taiki/generated/skills/.cache/registry.json`` o
      ``cache_dir`` si se indica; la copia caduca con ``ttl_seconds``.
    - Si la descarga falla pero existe caché del mismo origen, se devuelve la
      copia aunque esté caducada: el registro trae sus propios hashes y la
      verificación del bundle sigue siendo obligatoria.
    """
    origin = str(source) if source is not None else DEFAULT_REGISTRY_URL
    if not origin.startswith(("http://", "https://")):
        path = Path(origin).expanduser()
        try:
            raw = path.read_bytes()
        except OSError as exc:
            raise ValidationError(
                f"No se pudo leer el registro de skills {origin}: {exc}"
            ) from exc
        return _parse_registry(raw, origin)

    clock = now or time.time
    cache = _cache_path(root, cache_dir)
    cached = _read_cache(cache) if cache is not None else None
    if (
        not refresh
        and cached is not None
        and cached.get("source") == origin
        and isinstance(cached.get("registry"), dict)
    ):
        fetched_at = cached.get("fetched_at")
        age = clock() - float(fetched_at) if isinstance(fetched_at, (int, float)) else 0
        if 0 <= age <= max(0, int(ttl_seconds)):
            try:
                return validate_registry(cached["registry"])
            except ValidationError:
                cached = None

    fetcher = fetch or default_fetch
    try:
        raw = _bytes(fetcher(origin))
    except ValidationError:
        raise
    except Exception as exc:  # noqa: BLE001 - red/HTTP: se intenta la caché
        if cached is not None and isinstance(cached.get("registry"), dict):
            try:
                return validate_registry(cached["registry"])
            except ValidationError:
                pass
        raise ValidationError(
            f"No se pudo descargar el registro de skills {origin}: {exc}"
        ) from exc

    registry = _parse_registry(raw, origin)
    if cache is not None:
        _write_cache(
            cache,
            {
                "source": origin,
                "fetched_at": clock(),
                "sha256": hashlib.sha256(raw).hexdigest(),
                "registry": registry,
            },
        )
    return registry


def skill_file_url(
    name: str,
    file: str,
    *,
    ref: str = "main",
    base_url: str = RAW_BASE_URL,
) -> str:
    """URL raw de un fichero del bundle publicado en el repositorio.

    ``base_url`` puede apuntar a un mirror o a un fixture; si se pide otra
    ``ref`` se sustituye el segmento ``/main/`` del valor por defecto.
    """
    safe_name = urllib.parse.quote(str(name).strip(), safe="")
    safe_file = urllib.parse.quote(_safe_relative(file), safe="/")
    base = base_url.rstrip("/")
    if ref != "main" and base == RAW_BASE_URL:
        base = base.replace("/main/", f"/{urllib.parse.quote(ref, safe='')}/", 1)
    return f"{base}/{safe_name}/{safe_file}"


def fetch_bundle(
    name: str,
    registry: dict[str, Any],
    *,
    fetch: Fetch | None = None,
    ref: str = "main",
    base_url: str = RAW_BASE_URL,
) -> dict[str, Any]:
    """Descarga y verifica el bundle de una skill.

    Comprueba el SHA-256 de cada fichero y recalcula ``bundleHash``; rechaza
    ``.zip`` y cualquier contenido que no sea UTF-8. Devuelve los ficheros con
    su contenido, hash y URL de procedencia.
    """
    key = str(name or "").strip()
    entry = registry_entry(registry, key)
    files = [str(item) for item in entry.get("files", [])]
    if any(item.lower().endswith(".zip") for item in files):
        raise ValidationError(f"La skill {key} incluye un .zip no permitido")

    fetcher = fetch or default_fetch
    expected = {
        str(path): str(digest) for path, digest in (entry.get("sha256") or {}).items()
    }
    downloaded: list[dict[str, Any]] = []
    hashes: dict[str, str] = {}
    urls: dict[str, str] = {}
    for relative in files:
        path = _safe_relative(relative)
        url = skill_file_url(key, path, ref=ref, base_url=base_url)
        try:
            payload = _bytes(fetcher(url))
        except ValidationError:
            raise
        except Exception as exc:  # noqa: BLE001 - red/HTTP del fetch inyectable
            raise ValidationError(
                f"No se pudo descargar {key}/{path}: {exc}"
            ) from exc
        digest = hashlib.sha256(payload).hexdigest()
        if digest != expected.get(path):
            raise ValidationError(
                f"Hash incorrecto para {key}/{path}: esperado "
                f"{expected.get(path)}, obtenido {digest}"
            )
        try:
            content = payload.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise ValidationError(
                f"El fichero {key}/{path} no es texto UTF-8 válido"
            ) from exc
        hashes[path] = digest
        urls[path] = url
        downloaded.append({"path": path, "content": content, "sha256": digest})

    actual_bundle = bundle_hash(hashes)
    if actual_bundle != entry.get("bundleHash"):
        raise ValidationError(
            f"bundleHash incorrecto para {key}: esperado "
            f"{entry.get('bundleHash')}, obtenido {actual_bundle}"
        )
    return {
        "name": key,
        "source": entry.get("source"),
        "skillPath": entry.get("skillPath"),
        "commitSha": entry.get("commitSha"),
        "bundleHash": actual_bundle,
        "review": entry.get("review"),
        "securityCheck": entry.get("securityCheck"),
        "files": downloaded,
        "urls": urls,
        "skill_url": urls.get("SKILL.md"),
    }


__all__ = [
    "CACHE_RELATIVE",
    "DEFAULT_REGISTRY_URL",
    "DEFAULT_TTL_SECONDS",
    "RAW_BASE_URL",
    "bundle_hash",
    "default_fetch",
    "fetch_bundle",
    "load_registry",
    "registry_entry",
    "registry_skills",
    "skill_file_url",
    "validate_entry",
    "validate_registry",
]
