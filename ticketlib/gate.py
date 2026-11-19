"""Gates declarados del workspace y del ticket.

La herramienta MCP ``gate_run`` no es un shell: solo ejecuta comandos que ya
están declarados en ``verify.command`` del workspace o en un bloque
``gates`` del body del ticket. Aquí vive la resolución, no el despacho.
"""

from __future__ import annotations

import json
import re
import shlex
import subprocess
import time
from pathlib import Path
from typing import Any

from . import workspace
from .domain import TicketError, ValidationError

DEFAULT_TIMEOUT = 120.0
MAX_STREAM_CHARS = 8000
TRUNCATION_MARK = "\n…[truncado]"
_SHELL_META = re.compile(r"[|;&<>`]|\$\(")
_GATES_BLOCK = re.compile(r"```gates\s*\n(.*?)```", re.DOTALL)


def _entry(name: str, command: str | list[str]) -> dict[str, Any]:
    """Normaliza un comando declarado a ``{name, command, shell}``.

    Reproduce la regla de ``commands/release.py::_verify_command``: un array se
    ejecuta tal cual y una cadena con metacaracteres usa ``shell=True``.
    """
    if isinstance(command, (list, tuple)):
        argv = [str(item) for item in command if str(item).strip()]
        if not argv:
            raise ValidationError(f"El gate '{name}' no tiene comando")
        return {"name": name, "command": argv, "shell": False}
    text = str(command).strip()
    if not text:
        raise ValidationError(f"El gate '{name}' no tiene comando")
    if _SHELL_META.search(text):
        return {"name": name, "command": text, "shell": True}
    return {"name": name, "command": shlex.split(text), "shell": False}


def _ticket_body(conn: Any, ticket: str) -> str:
    row = conn.execute(
        "SELECT body FROM tickets WHERE code = ? AND archived = 0", (ticket,)
    ).fetchone()
    if row is None:
        raise TicketError(f"Ticket desconocido: {ticket}")
    return str(row["body"] or "")


def _parse_gates(body: str) -> list[dict[str, Any]]:
    """Lee el bloque ```gates del body: objeto ``{name: command}`` o lista."""
    match = _GATES_BLOCK.search(body or "")
    if match is None:
        return []
    try:
        data = json.loads(match.group(1))
    except json.JSONDecodeError as exc:
        raise ValidationError(
            "El bloque gates del ticket no es JSON válido: "
            f"{exc.msg} (línea {exc.lineno})"
        ) from exc
    entries: list[dict[str, Any]] = []
    if isinstance(data, dict):
        for name, command in data.items():
            entries.append(_entry(str(name), command))
    elif isinstance(data, list):
        for item in data:
            if not isinstance(item, dict) or not item.get("name"):
                raise ValidationError(
                    "Cada gate del ticket necesita {'name': ..., 'command': ...}"
                )
            entries.append(_entry(str(item["name"]), item["command"]))
    else:
        raise ValidationError(
            "El bloque gates debe ser un objeto {nombre: comando} o una lista"
        )
    return entries


def resolve_gates(
    conn: Any = None, ticket: str | None = None
) -> list[dict[str, Any]]:
    """Gates disponibles: ``verify.command`` y, si hay ticket, su bloque gates."""
    entries: list[dict[str, Any]] = []
    verify = workspace.verify_command()
    if verify:
        entries.append(_entry("verify", verify))
    if ticket:
        if conn is None:
            raise ValidationError("Hace falta la conexión para leer el ticket")
        entries.extend(_parse_gates(_ticket_body(conn, ticket)))
    if not entries:
        where = (
            "declara 'verify.command' en .taiki/config.json o un bloque "
            "```gates {nombre: comando}``` en el body del ticket"
        )
        raise ValidationError(f"No hay gates declarados: {where}")
    names = [entry["name"] for entry in entries]
    duplicated = {name for name in names if names.count(name) > 1}
    if duplicated:
        raise ValidationError(
            "Hay gates declarados con nombre repetido: "
            + ", ".join(sorted(duplicated))
        )
    return entries


def resolve_cwd(conn: Any = None, ticket: str | None = None) -> Path:
    """Worktree de la sesión del ticket si existe; si no, el árbol vivo."""
    if ticket and conn is not None:
        from .sessions import ticket_activity

        info = ticket_activity(conn, [ticket]).get(ticket, {})
        directory = info.get("directory")
        if directory and Path(directory).is_dir():
            return Path(directory)
        row = conn.execute(
            "SELECT l.ref AS ref FROM links l JOIN tickets t ON t.id = l.ticket_id "
            "WHERE t.code = ? AND l.kind = 'worktree' ORDER BY l.id DESC LIMIT 1",
            (ticket,),
        ).fetchone()
        if row and Path(row["ref"]).is_dir():
            return Path(row["ref"])
    return workspace.project_root()


def _clip(text: str | bytes | None) -> tuple[str, bool]:
    if text is None:
        return "", False
    if isinstance(text, bytes):
        text = text.decode("utf-8", "replace")
    if len(text) <= MAX_STREAM_CHARS:
        return text, False
    return text[-MAX_STREAM_CHARS:] + TRUNCATION_MARK, True


def run_gate(
    entry: dict[str, Any], cwd: str | Path, timeout: float | None = None
) -> dict[str, Any]:
    """Ejecuta un gate declarado y devuelve su salida acotada."""
    folder = Path(cwd)
    if not folder.is_dir():
        raise ValidationError(f"El directorio de ejecución no existe: {folder}")
    kwargs: dict[str, Any] = {
        "cwd": str(folder),
        "capture_output": True,
        "text": True,
        "timeout": DEFAULT_TIMEOUT if timeout is None else float(timeout),
        "stdin": subprocess.DEVNULL,
    }
    started = time.monotonic()
    timed_out = False
    try:
        if entry.get("shell"):
            result = subprocess.run(entry["command"], shell=True, **kwargs)
        else:
            result = subprocess.run(entry["command"], shell=False, **kwargs)
        code: int | None = result.returncode
        stdout, stderr = result.stdout, result.stderr
    except subprocess.TimeoutExpired as exc:
        timed_out = True
        code = None
        stdout, stderr = exc.stdout, exc.stderr
    elapsed = int((time.monotonic() - started) * 1000)
    out, cut_out = _clip(stdout)
    err, cut_err = _clip(stderr)
    return {
        "name": entry["name"],
        "command": entry["command"],
        "cwd": str(folder),
        "exit": code,
        "stdout": out,
        "stderr": err,
        "durationMs": elapsed,
        "truncated": cut_out or cut_err,
        "timedOut": timed_out,
    }
