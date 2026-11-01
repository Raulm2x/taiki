"""Enlaces ``ticket://`` y su handler de escritorio.

``taiki scheme-install`` registra un ``.desktop`` que abre
``ticket://open/T-0001`` con el propio CLI, de modo que un hipervínculo OSC 8
pueda lanzar la sesión del agente desde cualquier terminal.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

from .domain import ValidationError

SCHEME = "ticket"
OPEN_ACTION = "open"
DESKTOP_NAME = "ticket-open.desktop"


def scheme_url(code: str) -> str:
    """URL del enlace para un código de ticket."""
    return f"{SCHEME}://{OPEN_ACTION}/{code}"


def applications_dir() -> Path:
    base = os.environ.get("XDG_DATA_HOME") or str(Path.home() / ".local" / "share")
    return Path(base) / "applications"


def desktop_path() -> Path:
    return applications_dir() / DESKTOP_NAME


def parse_target(value: str) -> str:
    """Extrae el código de ticket de una URL ``ticket://`` o de un código."""
    text = (value or "").strip()
    if not text:
        raise ValidationError("Falta el ticket o la URL")

    if text.startswith(f"{SCHEME}:"):
        rest = text[len(f"{SCHEME}:") :].lstrip("/")
        if "/" in rest:
            action, _, target = rest.partition("/")
            if action and action != OPEN_ACTION:
                raise ValidationError(f"Acción no soportada: {action}")
            text = target
        else:
            text = rest
    text = text.split("?", 1)[0].split("#", 1)[0]
    if not text:
        raise ValidationError("La URL no contiene ningún ticket")
    return text


def _cli_executable() -> str:
    preferred = Path.home() / ".local" / "bin" / "taiki"
    if preferred.exists():
        return str(preferred)
    found = shutil.which("taiki")
    if found:
        return found
    return "taiki"


def desktop_entry() -> str:
    return (
        "[Desktop Entry]\n"
        "Type=Application\n"
        "Name=Ticket: abrir sesión del agente\n"
        "Comment=Abre la sesión de agente asociada a un ticket\n"
        f"Exec={_cli_executable()} open %u\n"
        "Terminal=false\n"
        "NoDisplay=true\n"
        "StartupNotify=false\n"
        f"MimeType=x-scheme-handler/{SCHEME};\n"
    )


def _update_database() -> None:
    update = shutil.which("update-desktop-database")
    if update:
        subprocess.run(
            [update, str(applications_dir())],
            capture_output=True,
            check=False,
        )


def install_scheme() -> Path:
    """Registra el handler ``ticket://`` y lo deja como predeterminado."""
    target = desktop_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(desktop_entry(), encoding="utf-8")
    xdg_mime = shutil.which("xdg-mime")
    if xdg_mime:
        subprocess.run(
            [xdg_mime, "default", DESKTOP_NAME, f"x-scheme-handler/{SCHEME}"],
            capture_output=True,
            check=False,
        )
    _update_database()
    return target


def uninstall_scheme() -> bool:
    """Elimina el handler. Devuelve ``True`` si existía."""
    target = desktop_path()
    existed = target.exists()
    if existed:
        target.unlink()
        _update_database()
    return existed


def scheme_installed() -> bool:
    return desktop_path().exists()
