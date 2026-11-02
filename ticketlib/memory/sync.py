"""Sincroniza los artefactos derivados de memoria con OpenCode.

Los ficheros de ``.taiki/generated/`` son **derivados**: se regeneran desde la
base de datos y nunca se versionan (el ``.gitignore`` del workspace los
excluye). Este módulo no decide el contenido de la memoria; solo escribe el
brief, exporta los playbooks como skills y mantiene sincronizadas las listas
``instructions`` y ``skills`` del proyecto.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .. import workspace
from ..domain import TicketError
from .brief import write_brief
from .config import load_memory_config
from .playbooks import export_all

BRIEF_INSTRUCTION = ".taiki/generated/brief.md"
SKILLS_INSTRUCTION = ".taiki/generated/skills"
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
    """Regenera el brief, exporta playbooks y asegura los derivados ignorados."""
    root_path = _root(root)
    ensure_gitignore(root_path)
    result = write_brief(root_path, conn, config)
    cfg = load_memory_config(config=config)
    exported: list[str] = []
    removed: list[str] = []
    if cfg["skills_sync"]:
        skills = export_all(conn, root_path, config=cfg)
        exported = list(skills["exported"])
        removed = list(skills["removed"])
    return {
        "brief_path": str(result["path"]),
        "chars": len(result["text"]),
        "version": result["version"],
        "skills_path": str(root_path / SKILLS_INSTRUCTION),
        "skills_synced": bool(cfg["skills_sync"]),
        "playbooks": exported,
        "removed_skills": removed,
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
    """Añade el brief y los skills generados sin tocar el resto.

    Ambas entradas son relativas al proyecto y se insertan al final,
    conservando el orden de las claves existentes. Llamarla de nuevo no cambia
    el fichero.
    """
    root_path = _root(root)
    path = _opencode_config(root_path)
    data = _read_json(path)
    instructions, added = _append_entry(data, "instructions", BRIEF_INSTRUCTION)
    skills, skills_added = _append_entry(data, "skills", SKILLS_INSTRUCTION)
    if not added and not skills_added:
        return {
            "path": str(path),
            "added": False,
            "skills_added": False,
        }
    if added:
        data["instructions"] = instructions
    if skills_added:
        data["skills"] = skills
    _write_json(path, data)
    return {
        "path": str(path),
        "added": added,
        "skills_added": skills_added,
    }


def _append_entry(
    data: dict[str, Any],
    key: str,
    entry: str,
) -> tuple[list[str], bool]:
    """Devuelve la lista existente y si hay que añadir ``entry``."""
    raw = data.get(key)
    if raw is None:
        values: list[str] = []
    elif isinstance(raw, list) and all(isinstance(item, str) for item in raw):
        values = raw
    else:
        raise TicketError(
            f"`{key}` en la configuración de OpenCode debe ser una lista de rutas"
        )
    if entry in values:
        return values, False
    return [*values, entry], True


def uninstall_opencode_memory(root: str | Path) -> Path | None:
    """Retira solo las entradas del brief y de skills; ``None`` si no estaban."""
    root_path = _root(root)
    touched: Path | None = None
    for path in (
        root_path / ".opencode" / "opencode.json",
        root_path / "opencode.json",
    ):
        if not path.is_file():
            continue
        data = _read_json(path)
        changed = False
        for key, entry in (
            ("instructions", BRIEF_INSTRUCTION),
            ("skills", SKILLS_INSTRUCTION),
        ):
            raw = data.get(key)
            if not isinstance(raw, list) or entry not in raw:
                continue
            remaining = [item for item in raw if item != entry]
            if remaining:
                data[key] = remaining
            else:
                data.pop(key, None)
            changed = True
        if changed:
            _write_json(path, data)
            touched = path
    return touched


__all__ = [
    "BRIEF_INSTRUCTION",
    "GENERATED_IGNORE",
    "SKILLS_INSTRUCTION",
    "ensure_gitignore",
    "install_opencode_memory",
    "sync_generated",
    "uninstall_opencode_memory",
]
