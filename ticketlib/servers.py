"""Inventario de servidores MCP locales: vivos, al día y con dueño.

Un servidor MCP es un proceso de larga vida que OpenCode lanza para servir
herramientas. Al reiniciar el servicio o cerrar sesiones pueden quedar procesos
vivos sin dueño: este módulo los detecta sin confundir «inactivo» con
«huérfano». Una sesión idle conserva su padre vivo, así que nunca se toca.
"""

from __future__ import annotations

import os
import signal
import time
from pathlib import Path
from typing import Any, Callable

from . import db, runtime
from .util import now

MARKERS = (
    "taiki/server.py",
    "tickets/server.py",
    "tickets-mcp",
    "taiki-mcp",
    "tickets/mcp",
)
STATES = (
    "al día",
    "sin registro",
    "duplicado",
    "desactualizado",
    "huérfano",
    "muerto",
)
SELF = os.getpid()

# El servicio de OpenCode lanza los MCP como hijos directos.
SERVICE_MARKERS = ("opencode serve",)
SHELLS = {"bash", "sh", "zsh", "fish", "dash", "env", "timeout", "sudo"}
SCRIPT_SUFFIXES = (".py", ".js", ".mjs", ".cjs", ".ts")


def process_info(pid: int) -> dict[str, Any] | None:
    """Datos de ``/proc``: ppid, cmdline y si sigue vivo. ``None`` si no."""
    try:
        stat = Path(f"/proc/{pid}/stat").read_text(encoding="utf-8")
    except OSError:
        return None
    # ``comm`` puede llevar espacios y paréntesis: se corta tras el último ')'.
    rest = stat.rsplit(")", 1)[-1].split()
    if not rest or rest[0] == "Z":
        return None
    try:
        ppid = int(rest[1])
    except (IndexError, ValueError):
        return None
    try:
        raw = Path(f"/proc/{pid}/cmdline").read_bytes()
    except OSError:
        return None
    try:
        cwd = os.readlink(f"/proc/{pid}/cwd")
    except OSError:
        cwd = None
    return {
        "pid": pid,
        "ppid": ppid,
        "cmdline": raw.replace(b"\0", b" ").decode(errors="replace").strip(),
        "cwd": cwd,
    }


def is_server(cmdline: str) -> bool:
    """¿La línea de comandos corresponde a un servidor MCP de tickets?"""
    return any(marker in (cmdline or "") for marker in MARKERS)


def is_service(cmdline: str) -> bool:
    """¿Es el proceso del servicio de OpenCode (el padre de los MCP)?"""
    return any(marker in (cmdline or "") for marker in SERVICE_MARKERS)


def looks_like_mcp(cmdline: str) -> bool:
    """Heurística para hijos del servicio que parecen servidores MCP."""
    text = (cmdline or "").strip().lower()
    if not text:
        return False
    binary = text.split()[0].rsplit("/", 1)[-1]
    if binary in SHELLS:
        return False
    return any(
        hint in text
        for hint in ("mcp", "server.py", "server.js", "server.mjs", "server.ts")
    )


def process_name(info: dict[str, Any]) -> str:
    """Nombre legible del servidor: carpeta del script o token con «mcp»."""
    tokens = str(info.get("cmdline") or "").split()
    for token in tokens:
        if token.endswith(SCRIPT_SUFFIXES):
            return Path(token).parent.name or Path(token).stem
    for token in tokens:
        if "mcp" in token.lower():
            return Path(token).name
    return "mcp"


def discover(
    process: Callable[[int], dict[str, Any] | None] = process_info,
    pids: list[int] | None = None,
) -> dict[int, dict[str, Any]]:
    """Procesos MCP según ``/proc``: marcadores propios o hijos del servicio."""
    if pids is None:
        try:
            pids = [int(name) for name in os.listdir("/proc") if name.isdigit()]
        except OSError:
            return {}
    infos = {}
    for pid in pids:
        info = process(pid)
        if info:
            infos[pid] = info
    services = {
        pid for pid, info in infos.items() if is_service(info["cmdline"])
    }
    found: dict[int, dict[str, Any]] = {}
    for pid, info in infos.items():
        if is_server(info["cmdline"]):
            found[pid] = info
        elif info["ppid"] in services and looks_like_mcp(info["cmdline"]):
            found[pid] = info
    return found


def hygiene(
    conn: Any,
    process: Callable[[int], dict[str, Any] | None] = process_info,
) -> int:
    """Borra filas de procesos que ya no existen. No mata nada."""
    removed = 0
    for row in conn.execute("SELECT pid FROM mcp_servers").fetchall():
        pid = row["pid"]
        if pid == SELF:
            continue
        info = process(pid)
        if info is None or not is_server(info["cmdline"]):
            conn.execute("DELETE FROM mcp_servers WHERE pid = ?", (pid,))
            removed += 1
    return removed


def register(
    conn: Any,
    name: str = "taiki",
    version: str = "",
    connection: str | None = None,
    workspace: str | None = None,
) -> dict[str, Any]:
    """Registra (o refresca) el proceso actual en el inventario global."""
    info = process_info(SELF) or {"ppid": 0}
    timestamp = now()
    conn.execute(
        "INSERT INTO mcp_servers(pid, ppid, name, fingerprint, version, "
        "started_at, heartbeat_at, connection, workspace) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?) "
        "ON CONFLICT(pid) DO UPDATE SET ppid = excluded.ppid, "
        "name = excluded.name, fingerprint = excluded.fingerprint, "
        "version = excluded.version, heartbeat_at = excluded.heartbeat_at, "
        "connection = COALESCE(excluded.connection, mcp_servers.connection), "
        "workspace = COALESCE(excluded.workspace, mcp_servers.workspace)",
        (
            SELF,
            info["ppid"],
            name,
            runtime.code_fingerprint(),
            version or None,
            timestamp,
            timestamp,
            connection,
            workspace,
        ),
    )
    hygiene(conn)
    return {
        "pid": SELF,
        "ppid": info["ppid"],
        "started_at": timestamp,
        "workspace": workspace,
    }


def heartbeat(conn: Any, connection: str | None = None) -> None:
    """Marca actividad; es informativo, nunca decide si un proceso es huérfano."""
    conn.execute(
        "UPDATE mcp_servers SET heartbeat_at = ?, "
        "connection = COALESCE(?, connection) WHERE pid = ?",
        (now(), connection, SELF),
    )


def classify(
    *,
    alive: bool,
    looks_like_server: bool,
    ppid: int | None,
    parent_alive: bool,
    fingerprint: str | None,
    current: str,
) -> str:
    """Estado de un proceso; pura para poder probarla sin ``/proc``."""
    if not alive or not looks_like_server:
        return "muerto"
    if ppid is None or ppid == 1 or not parent_alive:
        return "huérfano"
    if fingerprint and fingerprint != current:
        return "desactualizado"
    return "al día"


def inventory(
    conn: Any,
    process: Callable[[int], dict[str, Any] | None] = process_info,
    current: str | None = None,
    live: dict[int, dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    """Une el registro con los procesos reales y clasifica cada uno."""
    current = current or runtime.code_fingerprint()
    rows = {
        row["pid"]: dict(row)
        for row in conn.execute("SELECT * FROM mcp_servers ORDER BY pid")
    }
    # Un proceso que arrancó antes de existir el registro se identifica por el
    # handshake de `meta`: así sigue avisando si su código quedó viejo.
    meta_pid = db.meta_get(conn, "mcp_pid")
    meta_fp = db.meta_get(conn, "mcp_fingerprint")
    meta_started = db.meta_get(conn, "mcp_started")
    if live is None:
        live = discover(process)
    entries: list[dict[str, Any]] = []

    for pid in sorted(set(rows) | set(live)):
        row = rows.get(pid, {})
        info = live.get(pid)
        alive = info is not None
        fingerprint = row.get("fingerprint")
        started_at = row.get("started_at")
        if not row and meta_pid and meta_pid.isdigit() and int(meta_pid) == pid:
            fingerprint = fingerprint or meta_fp
            started_at = started_at or meta_started
        ppid = info["ppid"] if info else row.get("ppid")
        parent = process(ppid) if ppid else None
        state = classify(
            alive=alive,
            looks_like_server=is_server(info["cmdline"]) if info else False,
            ppid=ppid,
            parent_alive=parent is not None,
            fingerprint=fingerprint,
            current=current,
        )
        if state == "al día" and pid not in rows and fingerprint is None:
            state = "sin registro"
        entries.append(
            {
                "pid": pid,
                "ppid": ppid,
                "name": row.get("name")
                or (process_name(info) if info else "taiki"),
                "fingerprint": fingerprint,
                "version": row.get("version"),
                "started_at": started_at,
                "heartbeat_at": row.get("heartbeat_at"),
                "connection": row.get("connection"),
                "workspace": row.get("workspace")
                or (info.get("cwd") if info else None),
                "registered": pid in rows,
                "state": state,
                "base_state": state,
            }
        )

    groups: dict[tuple[str, int | None, str | None], list[dict[str, Any]]] = {}
    for entry in entries:
        if entry["state"] in ("al día", "sin registro", "desactualizado"):
            groups.setdefault(
                (
                    entry["name"],
                    entry["ppid"],
                    entry.get("workspace"),
                ),
                [],
            ).append(entry)
    for group in groups.values():
        if len(group) < 2:
            continue
        newest = max(group, key=lambda item: item["pid"])
        for entry in group:
            entry["state"] = "duplicado"
            entry["duplicate_of"] = newest["pid"]
    return entries


def summary(entries: list[dict[str, Any]]) -> dict[str, int]:
    """Cuenta por estado para audit y la cabecera de la TUI."""
    counts = {
        "total": len(entries),
        "orphans": 0,
        "stale": 0,
        "dead": 0,
        "unregistered": 0,
        "duplicates": 0,
    }
    for entry in entries:
        state = entry["state"]
        if state == "huérfano":
            counts["orphans"] += 1
        elif state == "desactualizado":
            counts["stale"] += 1
        elif state == "muerto":
            counts["dead"] += 1
        elif state == "sin registro":
            counts["unregistered"] += 1
        elif state == "duplicado":
            counts["duplicates"] += 1
    return counts


def _wait_dead(
    pid: int,
    process: Callable[[int], dict[str, Any] | None],
    timeout: float,
    sleep: Callable[[float], None] = time.sleep,
    step: float = 0.05,
) -> bool:
    """Espera a que el proceso desaparezca (o al menos deja de existir)."""
    if process(pid) is None:
        return True
    deadline = time.monotonic() + max(0.0, timeout)
    while time.monotonic() < deadline:
        sleep(step)
        if process(pid) is None:
            return True
    return process(pid) is None


def _terminate(
    pid: int,
    process: Callable[[int], dict[str, Any] | None],
    killer: Callable[[int, int], None],
    grace: float,
    slug: float,
    escalate: bool,
    sleep: Callable[[float], None],
) -> bool:
    """SIGTERM, espera y (si hace falta) SIGKILL. ``True`` si murió."""
    try:
        killer(pid, signal.SIGTERM)
    except OSError:
        return False
    if _wait_dead(pid, process, grace, sleep):
        return True
    if not escalate:
        return False
    try:
        killer(pid, signal.SIGKILL)
    except OSError:
        return False
    return _wait_dead(pid, process, slug, sleep)


def prune(
    conn: Any,
    apply: bool = False,
    process: Callable[[int], dict[str, Any] | None] = process_info,
    killer: Callable[[int, int], None] = os.kill,
    current: str | None = None,
    live: dict[int, dict[str, Any]] | None = None,
    grace: float = 1.5,
    slug: float = 0.5,
    escalate: bool = True,
    sleep: Callable[[float], None] = time.sleep,
    duplicates: bool = False,
) -> dict[str, list[int]]:
    """Limpia registros muertos y termina huérfanos; nunca un proceso con dueño.

    Un proceso cuyo padre sigue vivo (aunque la sesión esté idle durante horas)
    no se toca. Los duplicados solo se terminan con ``duplicates=True`` y se
    conserva siempre el más reciente.
    """
    plan: dict[str, list[int]] = {
        "dead": [],
        "orphans": [],
        "killed": [],
        "stubborn": [],
        "duplicates": [],
        "kept": [],
    }
    entries = inventory(conn, process=process, current=current, live=live)
    current_pid = os.getpid()

    for entry in entries:
        pid = entry["pid"]
        state = entry["state"]

        if state == "muerto":
            plan["dead"].append(pid)
            if apply:
                conn.execute("DELETE FROM mcp_servers WHERE pid = ?", (pid,))
            continue

        if state == "duplicado":
            if not duplicates or pid == entry.get("duplicate_of") or pid == current_pid:
                plan["kept"].append(pid)
                continue
            plan["duplicates"].append(pid)
            if not apply:
                continue
            if _terminate(pid, process, killer, grace, slug, escalate, sleep):
                plan["killed"].append(pid)
                conn.execute("DELETE FROM mcp_servers WHERE pid = ?", (pid,))
            else:
                plan["stubborn"].append(pid)
            continue

        if state != "huérfano" or pid == current_pid:
            plan["kept"].append(pid)
            continue

        plan["orphans"].append(pid)
        if not apply:
            continue
        # Revalidar en el último momento: el padre pudo reaparecer.
        info = process(pid)
        parent = process(info["ppid"]) if info else None
        if not info or parent is not None:
            plan["kept"].append(pid)
            continue
        if _terminate(pid, process, killer, grace, slug, escalate, sleep):
            plan["killed"].append(pid)
            conn.execute("DELETE FROM mcp_servers WHERE pid = ?", (pid,))
        else:
            plan["stubborn"].append(pid)

    return plan
