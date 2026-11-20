"""Registro de AutoSkills: validación, hashes, bundles y caché con TTL."""

from __future__ import annotations

import hashlib
import json

from ticketlib.domain import ValidationError
from ticketlib.skills.registry import (
    CACHE_RELATIVE,
    RAW_BASE_URL,
    bundle_hash,
    fetch_bundle,
    load_registry,
    skill_file_url,
    validate_registry,
)
from tests.support import Harness

SKILL = "demo-skill"
FILES = {
    "SKILL.md": "---\nname: demo-skill\ndescription: Demo.\n---\n\n# Demo\n\nCuerpo.\n",
    "references/details.md": "detalle\n",
}
REGISTRY_URL = "https://example.test/autoskills/index.json"


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _entry(files: dict[str, str] | None = None, **overrides) -> dict:
    contents = dict(FILES if files is None else files)
    hashes = {path: _sha(text) for path, text in contents.items()}
    entry = {
        "source": "midudev/autoskills",
        "skillPath": f"midudev/autoskills/{SKILL}",
        "commitSha": "0123456789abcdef0123456789abcdef01234567",
        "files": list(contents),
        "sha256": hashes,
        "bundleHash": bundle_hash(hashes),
        "review": {"status": "approved", "flags": [], "summary": "ok"},
        "securityCheck": {"status": "ok", "findings": [], "summary": "ok"},
    }
    entry.update(overrides)
    return entry


def _registry(files: dict[str, str] | None = None, **overrides) -> dict:
    return {
        "version": 1,
        "generatedAt": "2026-10-14T00:00:00.000Z",
        "skills": {SKILL: _entry(files, **overrides)},
    }


def _fetcher(files: dict[str, str] | None = None, name: str = SKILL):
    contents = dict(FILES if files is None else files)
    prefix = f"{RAW_BASE_URL}/{name}/"

    def fetch(url: str) -> bytes:
        if not url.startswith(prefix):
            raise AssertionError(f"URL inesperada: {url}")
        relative = url[len(prefix) :]
        if relative not in contents:
            raise AssertionError(f"Fichero inesperado: {relative}")
        return contents[relative].encode("utf-8")

    return fetch


def _rejects(h: Harness, cases: list, message: str) -> None:
    rejected = 0
    for case in cases:
        try:
            case()
        except ValidationError:
            rejected += 1
    h.eq(rejected, len(cases), message)


def _validation(h: Harness) -> None:
    valid = validate_registry(_registry())
    h.ok(
        valid["skills"][SKILL]["files"] == list(FILES)
        and valid["skills"][SKILL]["bundleHash"]
        == bundle_hash(valid["skills"][SKILL]["sha256"]),
        "el registro válido conserva ficheros y bundleHash",
    )

    camel = _registry()
    camel["skills"] = {
        "emailAndPassword": _entry(),
        "twoFactor": _entry(),
        "react.19_hooks-v2": _entry(),
    }
    h.eq(
        sorted(validate_registry(camel)["skills"]),
        ["emailAndPassword", "react.19_hooks-v2", "twoFactor"],
        "el registro acepta nombres camelCase con puntos, guiones y guiones bajos",
    )

    def bad_sha() -> dict:
        data = _registry()
        data["skills"][SKILL]["sha256"]["SKILL.md"] = "no-es-sha"
        return validate_registry(data)

    def missing_hash() -> dict:
        data = _registry()
        data["skills"][SKILL]["sha256"].pop("references/details.md")
        return validate_registry(data)

    def bundle_bad() -> dict:
        data = _registry()
        data["skills"][SKILL]["bundleHash"] = "x" * 64
        return validate_registry(data)

    def commit_bad() -> dict:
        data = _registry()
        data["skills"][SKILL]["commitSha"] = "zzz"
        return validate_registry(data)

    def zip_file() -> dict:
        data = _registry(files={"SKILL.md": "x", "bundle.zip": "PK"})
        return validate_registry(data)

    def traversal() -> dict:
        data = _registry(files={"SKILL.md": "x", "../fuera.md": "y"})
        return validate_registry(data)

    _rejects(
        h,
        [
            lambda: validate_registry([]),
            lambda: validate_registry({"version": 0, "skills": {}}),
            lambda: validate_registry({"version": 1, "skills": {}}),
            lambda: validate_registry(
                {"version": 1, "skills": {"con espacio": _entry()}}
            ),
            lambda: validate_registry({"version": 1, "skills": {"a/b": _entry()}}),
            lambda: validate_registry({"version": 1, "skills": {"-empieza": _entry()}}),
            lambda: validate_registry(
                {"version": 1, "skills": {"a" * 101: _entry()}}
            ),
            bad_sha,
            missing_hash,
            bundle_bad,
            commit_bad,
            zip_file,
            traversal,
        ],
        "se rechazan registros mal formados, hashes inválidos, .zip y traversal",
    )


def _local(h: Harness) -> None:
    path = h.tmp / "index.json"
    path.write_text(json.dumps(_registry()), encoding="utf-8")
    loaded = load_registry(path)
    h.ok(
        loaded["version"] == 1 and list(loaded["skills"]) == [SKILL],
        "load_registry lee un JSON local y expone sus skills",
    )
    h.raises(
        ValidationError,
        lambda: load_registry(h.tmp / "no-existe.json"),
        "un registro local ausente falla con ValidationError",
    )


def _bundle(h: Harness) -> None:
    registry = _registry()
    bundle = fetch_bundle(SKILL, registry, fetch=_fetcher())
    h.ok(
        [item["path"] for item in bundle["files"]] == list(FILES)
        and bundle["files"][0]["content"] == FILES["SKILL.md"]
        and bundle["bundleHash"] == _entry()["bundleHash"],
        "el bundle conserva orden, contenido y bundleHash del registro",
    )
    h.eq(
        [item["sha256"] for item in bundle["files"]],
        [_sha(FILES[path]) for path in FILES],
        "el bundle verifica el SHA-256 de cada fichero",
    )
    h.ok(
        bundle["urls"]["SKILL.md"] == f"{RAW_BASE_URL}/{SKILL}/SKILL.md"
        and bundle["skill_url"].endswith("/SKILL.md")
        and skill_file_url(SKILL, "references/details.md")
        == f"{RAW_BASE_URL}/{SKILL}/references/details.md",
        "las URLs de procedencia apuntan a los ficheros raw",
    )

    tampered = dict(FILES)
    tampered["SKILL.md"] = "contenido alterado\n"
    h.raises(
        ValidationError,
        lambda: fetch_bundle(SKILL, registry, fetch=_fetcher(tampered)),
        "un SHA-256 que no coincide rechaza el bundle",
    )

    forged = _registry()
    forged["skills"][SKILL]["bundleHash"] = "0" * 64
    h.raises(
        ValidationError,
        lambda: fetch_bundle(SKILL, forged, fetch=_fetcher()),
        "un bundleHash que no coincide rechaza el bundle",
    )

    zipped = _registry()
    zipped["skills"][SKILL]["files"] = ["SKILL.md", "extra.zip"]
    zipped["skills"][SKILL]["sha256"]["extra.zip"] = _sha("PK")
    zipped["skills"][SKILL]["bundleHash"] = bundle_hash(
        zipped["skills"][SKILL]["sha256"]
    )
    h.raises(
        ValidationError,
        lambda: fetch_bundle(SKILL, zipped, fetch=_fetcher()),
        "fetch_bundle rechaza bundles con .zip",
    )
    h.raises(
        ValidationError,
        lambda: fetch_bundle("no-existe", registry, fetch=_fetcher()),
        "una skill desconocida no descarga nada",
    )


def _cache(h: Harness) -> None:
    payload = json.dumps(_registry()).encode("utf-8")
    calls = {"fetch": 0}
    clock = {"now": 1000.0}

    def fetch(url: str) -> bytes:
        calls["fetch"] += 1
        return payload

    first = load_registry(
        REGISTRY_URL,
        root=h.tmp,
        fetch=fetch,
        now=lambda: clock["now"],
        ttl_seconds=60,
    )
    h.ok(
        list(first["skills"]) == [SKILL]
        and (h.tmp / CACHE_RELATIVE / "registry.json").is_file(),
        "load_registry descarga la URL y escribe la caché en .taiki/generated",
    )

    clock["now"] += 10
    second = load_registry(
        REGISTRY_URL,
        root=h.tmp,
        fetch=fetch,
        now=lambda: clock["now"],
        ttl_seconds=60,
    )
    h.ok(
        calls["fetch"] == 1 and second["version"] == first["version"],
        "dentro del TTL se usa la caché sin red y devuelve el mismo registro",
    )
    clock["now"] += 100
    load_registry(
        REGISTRY_URL,
        root=h.tmp,
        fetch=fetch,
        now=lambda: clock["now"],
        ttl_seconds=60,
    )
    h.eq(calls["fetch"], 2, "al caducar el TTL se vuelve a descargar")

    def offline(url: str) -> bytes:
        raise OSError("sin red")

    stale = load_registry(
        REGISTRY_URL,
        root=h.tmp,
        fetch=offline,
        now=lambda: clock["now"],
        ttl_seconds=0,
    )
    h.eq(list(stale["skills"]), [SKILL], "si la red falla se usa la caché anterior")

    h.raises(
        ValidationError,
        lambda: load_registry(
            "https://example.test/otro.json",
            cache_dir=h.tmp / "sin-cache",
            fetch=offline,
        ),
        "sin caché del mismo origen, un fallo de red es ValidationError",
    )
    explicit = h.tmp / "cache-explicita"
    load_registry(
        "https://example.test/otro.json",
        cache_dir=explicit,
        fetch=lambda url: payload,
        now=lambda: 1.0,
    )
    h.ok(
        (explicit / "registry.json").is_file()
        and explicit.joinpath("registry.json")
        .read_text(encoding="utf-8")
        .startswith("{"),
        "cache_dir explícito tiene prioridad y guarda el registro en JSON",
    )


def run(h: Harness) -> None:
    _validation(h)
    _local(h)
    _bundle(h)
    _cache(h)
