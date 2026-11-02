"""Sincroniza los artefactos derivados de memoria con OpenCode.

Los ficheros de ``.taiki/generated/`` son **derivados**: se regeneran desde la
base de datos y nunca se versionan (el ``.gitignore`` del workspace los
excluye). Este módulo no decide el contenido de la memoria; solo escribe el
brief y mantiene sincronizada la lista ``instructions`` del proyecto.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .. import workspace
from ..domain import TicketError
from .brief import write_brief

BRIEF_INSTRUCTION = ".taiki/generated/brief.md"
GENERATED_IGNORE = "generated/"


def _root(value: str | Path) -> Path:
    return Path(value).expanduser()


def ensure_gitignore(root: str | Path) -> Path:
    """Añade ``generated/`` al ``.gitignore`` del workspace una sola vez."""
    path = _root(root) / ".taiki" / ".gitignore"
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.is_file():
        path.write_text(workspace.GITIGNORE + GENERATED_IGNORE + "\n", encoding="utf-8")
        return path
    content = path.read_text(encoding="utf-8")
    if any(line.strip() == GENERATED_IGNORE for line in content.splitlines()):
        return path
    if content and not content.endswith("\n"):
        content += "\n"
    path.write_text(content + GENERATED_IGNORE + "\n", encoding="utf-8")
    return path


def sync_generated(
    root: str | Path,
    conn: Any,
    config: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Regenera el brief y asegura que sus derivados quedan ignorados."""
    root_path = _root(root)
    ensure_gitignore(root_path)
    result = write_brief(root_path, conn, config)
    return {
        "brief_path": str(result["path"]),
        "chars": len(result["text"]),
        "version": result["version"],
    }


def _opencode_config(root: Path) -> Path:
    """Fichero de OpenCode que corresponde al proyecto.

    Respeta el que ya exista (el CLI puede haber escrito en cualquiera de las
    dos ubicaciones) y, si no hay ninguno, replica la elección de
    ``commands.mcp._opencode_config``.
    """
    nested = root / ".opencode" / "opencode.json"
    if nested.is_file():
        return nested
    plain = root / "opencode.json"
    if plain.is_file():
        return plain
    if (root / ".opencode").is_dir():
        return nested
    return plain


def _read_json(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise TicketError(
            f"No se pudo leer {path}: {exc}. Revisa la configuración de OpenCode."
        ) from exc
    if not isinstance(data, dict):
        raise TicketError(f"La configuración de OpenCode en {path} debe ser un objeto JSON")
    return data


def _write_json(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(data, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


def install_opencode_memory(root: str | Path) -> dict[str, Any]:
    """Añade el brief generado a ``instructions`` sin tocar el resto.

    La entrada es relativa al proyecto y se inserta al final, conservando el
    orden de las claves existentes. Llamarla de nuevo no cambia el fichero.
    """
    root_path = _root(root)
    path = _opencode_config(root_path)
    data = _read_json(path)
    raw = data.get("instructions")
    if raw is None:
        instructions: list[str] = []
    elif isinstance(raw, list) and all(isinstance(item, str) for item in raw):
        instructions = raw
    else:
        raise TicketError(
            f"`instructions` en {path} debe ser una lista de rutas de texto"
        )
    if BRIEF_INSTRUCTION in instructions:
        return {"path": str(path), "added": False}
    data["instructions"] = [*instructions, BRIEF_INSTRUCTION]
    _write_json(path, data)
    return {"path": str(path), "added": True}


def uninstall_opencode_memory(root: str | Path) -> Path | None:
    """Retira solo la entrada del brief; ``None`` si no estaba."""
    root_path = _root(root)
    for path in (
        root_path / ".opencode" / "opencode.json",
        root_path / "opencode.json",
    ):
        if not path.is_file():
            continue
        data = _read_json(path)
        raw = data.get("instructions")
        if not isinstance(raw, list) or BRIEF_INSTRUCTION not in raw:
            continue
        remaining = [item for item in raw if item != BRIEF_INSTRUCTION]
        if remaining:
            data["instructions"] = remaining
        else:
            data.pop("instructions", None)
        _write_json(path, data)
        return path
    return None


__all__ = [
    "BRIEF_INSTRUCTION",
    "GENERATED_IGNORE",
    "ensure_gitignore",
    "install_opencode_memory",
    "sync_generated",
    "uninstall_opencode_memory",
]
