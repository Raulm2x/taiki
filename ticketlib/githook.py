"""Integración con Git: autollenazado y guardia del mensaje de commit."""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any

from . import env as envlib
from .domain import TicketError
from .store.relations import TICKET_CODE_RE

HOOK_BEGIN = "# >>> taiki auto-link >>>"
HOOK_END = "# <<< taiki auto-link <<<"
HOOK_BLOCK = (
    f"{HOOK_BEGIN}\n"
    "if command -v taiki >/dev/null 2>&1; then\n"
    "    taiki auto-link --quiet || true\n"
    "fi\n"
    f"{HOOK_END}\n"
)
LEGACY_HOOK = ("# >>> tickets auto-link >>>", "# <<< tickets auto-link <<<")

COMMIT_MSG_BEGIN = "# >>> taiki commit-msg >>>"
COMMIT_MSG_END = "# <<< taiki commit-msg <<<"
COMMIT_MSG_BLOCK = (
    f"{COMMIT_MSG_BEGIN}\n"
    "if command -v taiki >/dev/null 2>&1; then\n"
    '    taiki hook-check-msg "$1" || exit 1\n'
    "fi\n"
    f"{COMMIT_MSG_END}\n"
)
LEGACY_COMMIT_MSG = (
    "# >>> tickets commit-msg >>>",
    "# <<< tickets commit-msg <<<",
)

PRE_PUSH_BEGIN = "# >>> taiki pre-push >>>"
PRE_PUSH_END = "# <<< taiki pre-push <<<"
PRE_PUSH_BLOCK = (
    f"{PRE_PUSH_BEGIN}\n"
    "if command -v taiki >/dev/null 2>&1; then\n"
    '    taiki hook-check-push "$@" || exit 1\n'
    "fi\n"
    f"{PRE_PUSH_END}\n"
)
LEGACY_PRE_PUSH = ("# >>> tickets pre-push >>>", "# <<< tickets pre-push <<<")

BYPASS_TOKEN = "[sin-ticket]"
DISABLED_VALUES = {"0", "off", "false", "no", "disabled"}


def git(*args: str, cwd: Path | None = None) -> str:
    """Ejecuta git y devuelve stdout, fallando con ``TicketError``."""
    result = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True)
    if result.returncode != 0:
        detail = result.stderr.strip() or f"git {' '.join(args)} falló"
        raise TicketError(detail)
    return result.stdout.strip()


def _git_dir(root: Path) -> Path:
    value = Path(git("rev-parse", "--git-dir", cwd=root))
    return value if value.is_absolute() else root / value


def _remove_block(content: str, begin: str, end: str) -> str:
    """Quita un bloque delimitado y las líneas en blanco que deje de más."""
    while begin in content:
        start = content.index(begin)
        try:
            stop = content.index(end, start) + len(end)
        except ValueError:  # bloque a medias: no se toca
            break
        content = content[:start] + content[stop:]
        content = content.replace("\n\n\n", "\n\n")
    return content


def _install(
    repo: str | Path,
    name: str,
    begin: str,
    block: str,
    legacy: tuple[str, str] | None = None,
) -> Path:
    root = Path(repo).expanduser().resolve()
    hooks = _git_dir(root) / "hooks"
    hooks.mkdir(parents=True, exist_ok=True)
    hook = hooks / name

    original = (
        hook.read_text(encoding="utf-8") if hook.exists() else "#!/bin/sh\n"
    )
    content = _remove_block(original, *legacy) if legacy else original
    if begin not in content:
        content = content.rstrip("\n") + "\n\n" + block
    content = content.rstrip("\n") + "\n"
    if content != original or not hook.exists():
        hook.write_text(content, encoding="utf-8")
        hook.chmod(0o755)
    return hook


def install_post_commit_hook(repo: str | Path = ".") -> Path:
    """Instala (idempotente) el autollenazado en el ``post-commit`` del repo."""
    return _install(repo, "post-commit", HOOK_BEGIN, HOOK_BLOCK, LEGACY_HOOK)


def install_commit_msg_hook(repo: str | Path = ".") -> Path:
    """Instala (idempotente) la guardia de ``commit-msg``: exige T-XXXX."""
    return _install(
        repo, "commit-msg", COMMIT_MSG_BEGIN, COMMIT_MSG_BLOCK, LEGACY_COMMIT_MSG
    )


def install_pre_push_hook(repo: str | Path = ".") -> Path:
    """Instala (idempotente) el bloqueo de push con tickets sin confirmar."""
    return _install(
        repo, "pre-push", PRE_PUSH_BEGIN, PRE_PUSH_BLOCK, LEGACY_PRE_PUSH
    )


def check_push(
    conn: Any,
    repo: str | Path = ".",
    refs_text: str = "",
    git_func: Any = None,
) -> dict[str, Any]:
    """Bloquea el push si algún commit nuevo referencia un ticket no confirmado."""
    git_func = git_func or git
    root = Path(repo).expanduser()
    blocked: list[dict[str, Any]] = []
    checked = 0

    for line in (refs_text or "").splitlines():
        parts = line.split()
        if len(parts) < 4:
            continue
        _local_ref, local_sha, _remote_ref, remote_sha = parts[:4]
        try:
            if set(remote_sha) == {"0"}:
                shas = git_func(
                    "rev-list", local_sha, "--not", "--remotes", cwd=root
                ).split()
            else:
                shas = git_func(
                    "rev-list", f"{remote_sha}..{local_sha}", cwd=root
                ).split()
        except TicketError:
            continue
        for sha in shas:
            try:
                subject = git_func(
                    "show", "-s", "--format=%s", sha, cwd=root
                )
            except TicketError:
                continue
            checked += 1
            result = check_commit_message(conn, subject)
            if result["ok"] or result.get("reason") == "no-code":
                continue
            blocked.append(
                {
                    "commit": sha,
                    "subject": subject,
                    "reason": result.get("reason"),
                    "codes": result.get("codes", []),
                    "missing": result.get("missing", []),
                    "unconfirmed": result.get("unconfirmed", []),
                }
            )
    return {"ok": not blocked, "checked": checked, "blocked": blocked}


def hooks_installed(repo: str | Path = ".") -> dict[str, bool]:
    """Indica qué hooks del sistema de tickets tiene el repositorio."""
    root = Path(repo).expanduser().resolve()
    try:
        hooks = _git_dir(root) / "hooks"
    except TicketError:
        return {
            "post-commit": False,
            "commit-msg": False,
            "pre-push": False,
        }
    found = {}
    for name, marker in (
        ("post-commit", HOOK_BEGIN),
        ("commit-msg", COMMIT_MSG_BEGIN),
        ("pre-push", PRE_PUSH_BEGIN),
    ):
        path = hooks / name
        found[name] = path.is_file() and marker in path.read_text(encoding="utf-8")
    return found


def check_commit_message(conn: Any, message: str) -> dict[str, Any]:
    """Exige un ticket existente en el asunto y avisa de los del cuerpo.

    Solo la primera línea (asunto) bloquea: el cuerpo puede citar códigos
    antiguos o renumerados al explicar el cambio. Se puede saltar con
    ``TAIKI_HOOK=off`` (o el alias ``TICKETS_HOOK``) o con ``[sin-ticket]`` en
    el propio mensaje.
    """
    disabled = str(envlib.get("HOOK", "")).strip().lower()
    if disabled in DISABLED_VALUES:
        return {"ok": True, "skipped": "env", "codes": [], "missing": [],
                "unconfirmed": [], "warnings": []}
    if BYPASS_TOKEN in (message or ""):
        return {"ok": True, "skipped": "bypass", "codes": [], "missing": [],
                "unconfirmed": [], "warnings": []}

    lines = [
        line
        for line in (message or "").splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    ]
    subject = lines[0] if lines else ""
    body = "\n".join(lines[1:])

    subject_codes = list(dict.fromkeys(TICKET_CODE_RE.findall(subject)))
    body_codes = list(dict.fromkeys(TICKET_CODE_RE.findall(body)))
    if not subject_codes:
        return {"ok": False, "reason": "no-code", "codes": [], "missing": [],
                "unconfirmed": [], "warnings": []}

    codes = list(dict.fromkeys([*subject_codes, *body_codes]))
    placeholders = ", ".join("?" for _ in codes)
    statuses = {
        row["code"]: row["status"]
        for row in conn.execute(
            f"SELECT code, status FROM tickets WHERE code IN ({placeholders})",
            codes,
        )
    }
    missing = [code for code in subject_codes if code not in statuses]
    if missing:
        return {
            "ok": False,
            "reason": "unknown-code",
            "codes": subject_codes,
            "missing": missing,
            "unconfirmed": [],
            "warnings": [],
        }

    unconfirmed = [
        code for code in subject_codes if statuses[code] != "confirmado"
    ]
    if unconfirmed:
        return {
            "ok": False,
            "reason": "unconfirmed",
            "codes": subject_codes,
            "missing": [],
            "unconfirmed": unconfirmed,
            "warnings": [],
        }

    warnings = [
        f"{code} no existe; solo se avisa porque está en el cuerpo"
        for code in body_codes
        if code not in statuses
    ]
    return {
        "ok": True,
        "reason": None,
        "codes": subject_codes,
        "missing": [],
        "unconfirmed": [],
        "warnings": warnings,
    }
