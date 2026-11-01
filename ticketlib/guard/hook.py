"""Adaptadores de herramientas y payloads de agentes."""

from __future__ import annotations

import os
from typing import Any

from .commands import command_is_mutating, path_guarded

WRITE_TOOLS = {
    "write",
    "edit",
    "patch",
    "multiedit",
    "apply_patch",
    "create",
    "writefile",
}
SHELL_TOOLS = {"bash", "shell", "execute", "run", "sh"}

# Nombres que usan otros agentes para las mismas acciones.
TOOL_ALIASES = {
    "write_file": "write",
    "create_file": "write",
    "writefile": "write",
    "edit_file": "edit",
    "replace": "edit",
    "multi_replace_string_in_file": "edit",
    "apply_patch": "patch",
    "run_shell_command": "bash",
    "run_command": "bash",
    "shell_command": "bash",
    "execute_command": "bash",
    "terminal": "bash",
}


def normalize_tool(name: str) -> str:
    """Nombre canónico de la herramienta (acepta alias de otros agentes)."""
    base = str(name or "").rsplit(".", 1)[-1].strip().lower()
    return TOOL_ALIASES.get(base, base)


def parse_hook_input(payload: Any) -> dict[str, Any]:
    """Extrae tool/path/command de un evento genérico de hook."""
    data = payload if isinstance(payload, dict) else {}
    raw = data.get("tool_input") or data.get("args") or data.get("input") or {}
    if not isinstance(raw, dict):
        raw = {}
    path = (
        raw.get("path")
        or raw.get("file_path")
        or raw.get("filePath")
        or raw.get("absolute_path")
        or raw.get("target_file")
    )
    command = (
        raw.get("command")
        or raw.get("cmd")
        or raw.get("shell_command")
        or raw.get("script")
    )
    session = (
        data.get("session_id")
        or data.get("sessionId")
        or data.get("conversation_id")
    )
    agent = (
        data.get("agent")
        or data.get("agent_name")
        or os.environ.get("TICKETS_AGENT")
        or "agente"
    )
    return {
        "tool": data.get("tool_name") or data.get("tool") or data.get("name") or "",
        "path": path if isinstance(path, str) else None,
        "command": command if isinstance(command, str) else None,
        "session": session if isinstance(session, str) else None,
        "agent": str(agent),
    }


def is_mutating(tool: str, path: str | None = None, command: str | None = None) -> bool:
    """Decide si una herramienta puede modificar el sistema."""
    name = normalize_tool(tool)
    if name in WRITE_TOOLS:
        return True
    if name in SHELL_TOOLS:
        if command is None:
            return True
        return command_is_mutating(command)
    return False
