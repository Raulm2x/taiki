"""Instalación opt-in del MCP de Taiki en un proyecto.

Taiki no registra un servidor MCP global: cada proyecto decide si lo quiere.
``taiki mcp install`` escribe la configuración del cliente en la raíz del
proyecto y activa el guard en ``.taiki/config.json``. ``uninstall`` deshace
ambos cambios.
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

from ticketlib import db, workspace
from ticketlib.domain import TicketError
from ticketlib.githook import (
    install_commit_msg_hook,
    install_post_commit_hook,
    install_pre_commit_hook,
    install_pre_push_hook,
)
from ticketlib.memory.sync import (
    install_opencode_memory,
    sync_generated,
    uninstall_opencode_memory,
)

MCP_NAME = "taiki"
LEGACY_NAME = "tickets"
SERVER = Path(__file__).resolve().parent.parent / "server.py"


def server_command() -> list[str]:
    """Comando absoluto y estable para arrancar el servidor MCP."""
    return ["python3", str(SERVER)]


def _opencode_config(root: Path) -> Path:
    """Fichero donde escribir si no está disponible el CLI de OpenCode."""
    nested = root / ".opencode" / "opencode.json"
    if (root / ".opencode").is_dir():
        return nested
    return root / "opencode.json"


def _read_json(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise TicketError(
            f"No se pudo leer {path}: {exc}. Usa `opencode mcp add` a mano."
        ) from exc
    return data if isinstance(data, dict) else {}


def _write_json(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(data, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


def _install_with_cli(root: Path) -> str | None:
    """Usa el CLI oficial de OpenCode si está disponible (preserva el resto)."""
    binary = shutil.which("opencode")
    if not binary:
        return None
    try:
        result = subprocess.run(
            [binary, "mcp", "add", MCP_NAME, "--", *server_command()],
            cwd=str(root),
            capture_output=True,
            text=True,
            timeout=30,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if result.returncode != 0:
        return None
    return (result.stdout or "").strip() or None


def _drop_server(root: Path, name: str) -> Path | None:
    """Quita una entrada de MCP del proyecto; ``None`` si no estaba."""
    for path in (root / "opencode.json", root / ".opencode" / "opencode.json"):
        if not path.is_file():
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        servers = (data.get("mcp") or {}).get("servers") or {}
        if name not in servers:
            continue
        servers.pop(name, None)
        _write_json(path, data)
        return path
    return None


def install_opencode(root: Path) -> dict[str, Any]:
    """Registra el MCP solo para este proyecto (retira la entrada histórica)."""
    output = _install_with_cli(root)
    _drop_server(root, LEGACY_NAME)
    if output is not None:
        return {"client": "opencode", "method": "cli", "detail": output}

    path = _opencode_config(root)
    data = _read_json(path)
    servers = data.setdefault("mcp", {}).setdefault("servers", {})
    servers.pop(LEGACY_NAME, None)
    servers[MCP_NAME] = {"type": "local", "command": server_command()}
    _write_json(path, data)
    return {"client": "opencode", "method": "file", "detail": str(path)}


def install_repo_hooks(root: str | Path) -> Path | None:
    """Instala los cuatro hooks de Taiki; ``None`` si ``root`` no es un repo Git.

    ``taiki mcp install`` lo usa para dejar el repo listo sin exigir un
    ``taiki hook-install`` aparte; este último sigue siendo la vía manual.
    """
    try:
        installed = [
            install_post_commit_hook(root),
            install_commit_msg_hook(root),
            install_pre_push_hook(root),
            install_pre_commit_hook(root),
        ]
    except TicketError as exc:
        print(
            f"OpenCode: no se instalaron los hooks ({exc}). "
            "Ejecuta `taiki hook-install` dentro del repo.",
            file=sys.stderr,
        )
        return None
    return installed[-1].parent


def sync_agent_profiles(
    root: str | Path,
    config: dict[str, Any],
    force: bool = False,
) -> dict[str, list[str]]:
    """Sincroniza los perfiles de ``.opencode/agents`` sin pisar los propios.

    Un perfil que ya existe y difiere del de Taiki se conserva y se informa en
    ``customized``; solo ``force`` lo sobrescribe. En un workspace live
    (``none``/``scope``) no hay worktrees que integrar: se copia todo menos
    ``merger.md`` y se retira el de destino solo si es idéntico al de origen
    (prístino) o si se fuerza. Uno personalizado se conserva.
    """
    source = Path(__file__).resolve().parent.parent / ".opencode" / "agents"
    dest = Path(root) / ".opencode" / "agents"
    dest.mkdir(parents=True, exist_ok=True)
    live = (config.get("isolation") or {}).get("default") in ("none", "scope")
    copied: list[str] = []
    skipped: list[str] = []
    removed: list[str] = []
    customized: list[str] = []
    for profile in sorted(source.glob("*.md")):
        if live and profile.name == "merger.md":
            skipped.append(profile.name)
            continue
        target = dest / profile.name
        text = profile.read_text(encoding="utf-8")
        if not target.exists():
            target.write_text(text, encoding="utf-8")
            copied.append(profile.name)
            continue
        if target.read_text(encoding="utf-8") == text:
            copied.append(profile.name)
        elif force:
            target.write_text(text, encoding="utf-8")
            copied.append(profile.name)
        else:
            customized.append(profile.name)
    if live:
        stale = dest / "merger.md"
        if stale.exists():
            source_merger = source / "merger.md"
            pristine = source_merger.is_file() and (
                stale.read_text(encoding="utf-8")
                == source_merger.read_text(encoding="utf-8")
            )
            if force or pristine:
                stale.unlink()
                removed.append(stale.name)
            else:
                customized.append(stale.name)
    return {
        "copied": copied,
        "skipped": skipped,
        "removed": removed,
        "customized": customized,
    }


def uninstall_opencode(root: Path) -> Path | None:
    """Quita el MCP del proyecto sin tocar el resto de su configuración."""
    removed = _drop_server(root, MCP_NAME)
    legacy = _drop_server(root, LEGACY_NAME)
    return removed or legacy


def install_antigravity() -> dict[str, Any]:
    """Antigravity solo ofrece configuración global: se pide expresamente."""
    binary = shutil.which("agy")
    if not binary:
        raise TicketError("No se encontró el CLI `agy`")
    result = subprocess.run(
        [binary, "mcp", "add", MCP_NAME, *server_command()],
        capture_output=True,
        text=True,
        timeout=30,
    )
    if result.returncode != 0:
        raise TicketError(
            (result.stderr or result.stdout or "agy mcp add falló").strip()
        )
    return {"client": "antigravity", "method": "cli", "detail": "global"}


def _workspace_or_create() -> tuple[Path, Path]:
    """Devuelve ``(raíz, .taiki)``, creando el workspace si aún no existe."""
    info = workspace.resolve()
    if info["workspace"] is not None:
        return info["workspace"].parent, info["workspace"]
    root = workspace.project_root()
    created = workspace.init_workspace(root)
    return root, created["workspace"]


def cmd_mcp_install(args: argparse.Namespace) -> int:
    root, folder = _workspace_or_create()
    config = workspace.load_config(folder)
    config["guard"] = True
    workspace.save_config(config, folder)

    result = install_opencode(root)
    print(f"OpenCode: MCP `{MCP_NAME}` instalado en {result['detail']}")
    hooks = install_repo_hooks(root)
    if hooks is not None:
        print(f"OpenCode: hooks instalados en {hooks}")
    agents_source = Path(__file__).resolve().parent.parent / ".opencode" / "agents"
    if not agents_source.is_dir():
        print(
            "OpenCode: el paquete no trae perfiles de agente; "
            "se conservan los existentes en .opencode/agents.",
            file=sys.stderr,
        )
    profiles = sync_agent_profiles(
        root, config, force=getattr(args, "force_agents", False)
    )
    live = (config.get("isolation") or {}).get("default") in ("none", "scope")
    suffix = "sin merger: isolation live" if live else "con merger"
    print(
        f"OpenCode: {len(profiles['copied'])} perfiles de agente "
        f"sincronizados ({suffix})"
    )
    if profiles["removed"]:
        print(
            "OpenCode: retirado "
            + ", ".join(profiles["removed"])
            + " (sin merger en isolation live)"
        )
    if profiles["customized"]:
        print(
            "OpenCode: perfiles personalizados conservados: "
            + ", ".join(profiles["customized"])
            + ". Usa `--force-agents` para sobrescribirlos.",
            file=sys.stderr,
        )

    conn = db.connect()
    try:
        synced = sync_generated(root, conn, config)
    finally:
        conn.close()
    memory = install_opencode_memory(root)
    print(
        f"OpenCode: brief generado en {synced['brief_path']} "
        f"({synced['chars']} chars, v{synced['version']})"
    )
    if synced["playbooks"]:
        print(
            f"OpenCode: {len(synced['playbooks'])} playbook(s) exportados en "
            f"{synced['skills_path']}"
        )
    if memory["added"]:
        print(f"OpenCode: brief añadido a instructions en {memory['path']}")
    if memory["skills_added"]:
        print(f"OpenCode: skills añadidos en {memory['path']}")

    if getattr(args, "antigravity", False):
        install_antigravity()
        print(
            "Antigravity: MCP instalado en la configuración global "
            "(ese cliente no admite alcance por proyecto)."
        )
    print(f"Guard activado en {folder / workspace.CONFIG_NAME}")
    return 0


def cmd_mcp_uninstall(args: argparse.Namespace) -> int:
    info = workspace.resolve()
    if info["workspace"] is None:
        print(workspace.MISSING, file=sys.stderr)
        return 1
    config = workspace.load_config(info["workspace"])
    config["guard"] = False
    workspace.save_config(config, info["workspace"])
    removed = uninstall_opencode(info["workspace"].parent)
    if removed is not None:
        print(f"OpenCode: MCP `{MCP_NAME}` retirado de {removed}")
    else:
        print("OpenCode: no había un MCP de Taiki en este proyecto")
    memory = uninstall_opencode_memory(info["workspace"].parent)
    if memory is not None:
        print(f"OpenCode: brief retirado de instructions en {memory}")
    if getattr(args, "antigravity", False):
        binary = shutil.which("agy")
        if binary:
            subprocess.run(
                [binary, "mcp", "remove", MCP_NAME],
                capture_output=True,
                text=True,
                timeout=30,
            )
            print("Antigravity: MCP retirado de la configuración global")
    print(f"Guard desactivado en {info['workspace']}")
    return 0
