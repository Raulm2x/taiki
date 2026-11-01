"""Presentación de tickets en texto, tablas formateadas, terminal y JSON."""

from __future__ import annotations

import json
import os
import sys
import textwrap
from pathlib import Path
from typing import Any

from . import agents, deeplink

STATUS_COLORS: dict[str, str] = {
    "nuevo": "36",
    "pendiente": "33",
    "en_progreso": "34",
    "completado": "32",
    "confirmado": "1;32",
    "bloqueado": "35",
    "cancelado": "2",
}

PRIORITY_COLORS: dict[str, str] = {
    "urgente": "1;31",
    "alta": "31",
    "media": "33",
    "baja": "2",
}


def is_color_enabled() -> bool:
    """Detecta si la salida estándar soporta secuencias de escape ANSI."""
    return sys.stdout.isatty() and not bool(os.environ.get("NO_COLOR"))


def is_link_enabled() -> bool:
    """Los hipervínculos OSC 8 requieren color y no estar desactivados."""
    return is_color_enabled() and not bool(os.environ.get("TICKET_NO_LINKS"))


def colorize(text: str, color_code: str | None, enabled: bool | None = None) -> str:
    """Aplica formato ANSI al texto si el color está habilitado."""
    active = is_color_enabled() if enabled is None else enabled
    if not active or not color_code:
        return text
    return f"\033[{color_code}m{text}\033[0m"


def hyperlink(text: str, url: str | None, enabled: bool | None = None) -> str:
    """Envuelve el texto en un enlace OSC 8 (Ctrl+clic en kitty/alacritty)."""
    active = is_link_enabled() if enabled is None else enabled
    if not active or not url:
        return text
    return f"\033]8;;{url}\033\\{text}\033]8;;\033\\"


def truncate(text: str, width: int) -> str:
    """Trunca el texto al ancho especificado añadiendo elipsis si lo supera."""
    return text if len(text) <= width else text[: width - 1] + "…"


def render_agent(
    ticket: dict[str, Any],
    session_id: str | None,
    use_color: bool | None = None,
    width: int = 18,
) -> str:
    """Columna AGENTE: color por agente y enlace a la sesión si sigue viva.

    Sin sesión existente el nombre va en gris y nunca se enlaza.
    """
    name = agents.agent_name(ticket) or "-"
    plain = truncate(name, width)
    if plain == "-":
        return plain
    if not session_id:
        return colorize(plain, agents.NO_SESSION_COLOR, enabled=use_color)
    rendered = colorize(plain, agents.agent_color(name), enabled=use_color)
    return hyperlink(rendered, deeplink.scheme_url(ticket["code"]), enabled=use_color)


def ticket_title(ticket: dict[str, Any]) -> str:
    """Título con prefijo de árbol y progreso de épica."""
    prefix = ticket.get("tree_prefix") or ""
    progress = ticket.get("progress") or {}
    badge = ""
    if progress.get("total"):
        badge = f"[{progress['done']}/{progress['total']}] "
    return f"{prefix}{badge}{ticket['title']}"


def render_table(
    tickets: list[dict[str, Any]],
    use_color: bool | None = None,
    session_map: dict[str, str | None] | None = None,
) -> str:
    """Genera una tabla ASCII alineada con colores opcionales.

    ``session_map`` asocia código de ticket con su sesión viva; si falta o no
    contiene el código, el agente se muestra en gris y sin enlace.
    """
    if not tickets:
        return ""
    sessions_by_code = session_map or {}
    headers = (
        "CÓDIGO",
        "ESTADO",
        "PRIORIDAD",
        "TIPO",
        "PROYECTO",
        "AGENTE",
        "TÍTULO",
    )
    rows = [
        [
            ticket.get("display_code") or ticket["code"],
            ticket["status"],
            ticket["priority"],
            ticket["kind"],
            ticket.get("project") or "-",
            truncate(agents.agent_name(ticket) or "-", 18),
            truncate(ticket_title(ticket), 70),
        ]
        for ticket in tickets
    ]
    widths = [
        max(len(headers[i]), *(len(row[i]) for row in rows))
        for i in range(len(headers))
    ]
    lines = [
        "  ".join(header.ljust(widths[i]) for i, header in enumerate(headers)),
        "  ".join("-" * width for width in widths),
    ]
    for ticket, row in zip(tickets, rows):
        cells = [row[i].ljust(widths[i]) for i in range(len(headers))]
        cells[1] = colorize(
            cells[1], STATUS_COLORS.get(ticket["status"]), enabled=use_color
        )
        cells[2] = colorize(
            cells[2], PRIORITY_COLORS.get(ticket["priority"]), enabled=use_color
        )
        agent = render_agent(
            ticket,
            sessions_by_code.get(ticket["code"]),
            use_color=use_color,
        )
        cells[5] = agent + " " * (widths[5] - len(row[5]))
        lines.append("  ".join(cells))
    return "\n".join(lines)


def print_table(
    tickets: list[dict[str, Any]],
    use_color: bool | None = None,
    session_map: dict[str, str | None] | None = None,
) -> None:
    """Imprime directamente la tabla formateada en stdout."""
    output = render_table(tickets, use_color=use_color, session_map=session_map)
    if output:
        print(output)


def _json_default(value: Any) -> Any:
    if isinstance(value, Path):
        return str(value)
    raise TypeError(f"Object of type {type(value).__name__} is not JSON serializable")


def to_json(data: Any, *, compact: bool = False) -> str:
    """Serializa a JSON; compacto para agentes, con sangría para humanos."""
    if compact:
        return json.dumps(
            data,
            ensure_ascii=False,
            separators=(",", ":"),
            default=_json_default,
        )
    return json.dumps(
        data, ensure_ascii=False, indent=2, default=_json_default
    )


def _indent(text: str, prefix: str = "  ") -> str:
    """Sangra un bloque de texto multilínea."""
    return textwrap.indent(text, prefix)


def render_line(ticket: dict[str, Any], use_color: bool | None = None) -> str:
    """Genera una línea compacta representativa del ticket."""
    project = ticket.get("project") or "-"
    status_str = colorize(
        ticket["status"], STATUS_COLORS.get(ticket["status"]), enabled=use_color
    )
    priority_str = colorize(
        ticket["priority"], PRIORITY_COLORS.get(ticket["priority"]), enabled=use_color
    )
    return (
        f"{ticket['code']}  {status_str}  {priority_str}  "
        f"{ticket['kind']}  {project}  {ticket['title']}"
    )


def render_detail(
    ticket: dict[str, Any],
    comments: list[dict[str, Any]] | None = None,
    links: list[dict[str, Any]] | None = None,
    session_id: str | None = None,
) -> str:
    """Formatea la vista detallada de un ticket para la terminal."""
    lines = [
        f"{ticket['code']}  [{ticket['status']}]  "
        f"prioridad {ticket['priority']}  tipo {ticket['kind']}",
        ticket["title"],
    ]

    if ticket.get("body"):
        lines += ["", _indent(ticket["body"])]

    meta: list[str] = []
    if ticket.get("archived"):
        meta.append(
            "Archivado: fuera de la tabla visible "
            "(taiki list --archived para verlo)"
        )
    if ticket.get("project"):
        meta.append(f"Proyecto: {ticket['project']}")
    if ticket.get("path"):
        meta.append(f"Ruta: {ticket['path']}")
    if ticket.get("parent_code"):
        meta.append(f"Padre: {ticket['parent_code']}")
    progress = ticket.get("progress") or {}
    if progress.get("total"):
        meta.append(f"Progreso: {progress['done']}/{progress['total']}")
    meta.append(f"Creado: {ticket['created_at']} por {ticket['created_by']}")
    if ticket.get("assigned_to"):
        meta.append(f"Asignado: {ticket['assigned_to']}")
    elif ticket.get("last_actor"):
        meta.append(f"Agente: {ticket['last_actor']}")
    if ticket.get("started_at"):
        meta.append(f"Iniciado: {ticket['started_at']}")
    if ticket.get("completed_at"):
        meta.append(f"Completado: {ticket['completed_at']}")
    if ticket.get("confirmed_at"):
        meta.append(f"Confirmado: {ticket['confirmed_at']}")
    if ticket.get("iterations"):
        meta.append(f"Iteraciones: {ticket['iterations']}")
    commits = ticket.get("commits") or []
    if commits:
        label = commits[0][:9]
        if len(commits) > 1:
            label += f" (+{len(commits) - 1})"
        meta.append(f"Commit: {label}")
    if ticket.get("tags"):
        meta.append("Etiquetas: " + ", ".join(ticket["tags"]))
    if session_id:
        meta.append(
            "Sesión: " + hyperlink(session_id, deeplink.scheme_url(ticket["code"]))
        )
    lines += ["", *meta]

    if ticket.get("resolution"):
        lines += ["", "Resolución:", _indent(ticket["resolution"])]

    subtasks = ticket.get("subtasks") or []
    if subtasks:
        lines += ["", "Subtareas:"]
        for sub in subtasks:
            lines.append(f"  {sub['code']}  [{sub['status']}]  {sub['title']}")

    resolved_links = links if links is not None else (ticket.get("links") or [])
    if resolved_links:
        lines += ["", "Enlaces:"]
        lines += [f"  {link['kind']}: {link['ref']}" for link in resolved_links]

    resolved_comments = (
        comments if comments is not None else (ticket.get("comments") or [])
    )
    if resolved_comments:
        lines += ["", "Comentarios:"]
        for comment in resolved_comments:
            lines.append(f"  [{comment['ts']}] {comment['author']}: {comment['body']}")

    events = ticket.get("events") or []
    if events:
        lines += ["", "Historial:"]
        for event in events:
            detail = event["action"]
            if event["from_status"] or event["to_status"]:
                detail += f" {event['from_status'] or '-'} → {event['to_status'] or '-'}"
            note = f" — {event['note']}" if event["note"] else ""
            lines.append(f"  [{event['ts']}] {event['actor']} ({event['actor_type']}): {detail}{note}")

    return "\n".join(lines)


def render_stats(data: dict[str, Any]) -> str:
    """Formatea métricas y agregados estadísticos del sistema."""
    lines = [f"Total: {data['total']}  Abiertos: {data['abiertos']}"]
    if data.get("scratch"):
        lines.append(f"Scratch (temporales, ocultos): {data['scratch']}")

    def block(title: str, values: dict[str, int]) -> None:
        lines.append("")
        lines.append(title)
        width = max((len(key) for key in values), default=0)
        for key, value in values.items():
            lines.append(f"  {key.ljust(width)}  {value}")

    block("Por estado:", data["por_estado"])
    block("Por tipo:", data["por_tipo"])
    block("Por prioridad:", data["por_prioridad"])
    block("Por proyecto:", data["por_proyecto"])
    return "\n".join(lines)


# Aliases privados para compatibilidad con código existente
_color = colorize
_truncate = truncate
_print_table = print_table

__all__ = [
    "PRIORITY_COLORS",
    "STATUS_COLORS",
    "colorize",
    "hyperlink",
    "is_color_enabled",
    "is_link_enabled",
    "print_table",
    "render_agent",
    "render_detail",
    "render_line",
    "render_stats",
    "render_table",
    "to_json",
    "truncate",
]
