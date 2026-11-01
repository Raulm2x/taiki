"""Contenido del panel lateral: detalle de ticket y actividad reciente.

Sin curses: devuelve líneas con un estilo simbólico para que la vista decida
los colores. Así se puede probar el contenido sin abrir una terminal.
"""

from __future__ import annotations

import textwrap
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

from ticketlib import agents

STYLES = ("normal", "title", "label", "value", "dim", "accent", "status", "priority")


@dataclass(frozen=True)
class Line:
    """Una línea del panel con su estilo."""

    text: str
    style: str = "normal"


def wrap(text: str, width: int, max_lines: int | None = None) -> list[str]:
    """Ajusta ``text`` a ``width`` columnas, recortando si hace falta."""
    width = max(8, width)
    chunks: list[str] = []
    for raw in (text or "").splitlines() or [""]:
        pieces = textwrap.wrap(
            raw.strip(),
            width=width,
            break_long_words=True,
            break_on_hyphens=False,
        ) or [""]
        chunks.extend(pieces)
    if max_lines is not None and len(chunks) > max_lines:
        chunks = chunks[:max_lines]
        last = chunks[-1]
        if len(last) >= width:
            chunks[-1] = last[: max(0, width - 1)] + "…"
        else:
            chunks[-1] = last + "…"
    return chunks


FOLD = str.maketrans(
    "áàäâãéèëêíìïîóòöôõúùüûñç",
    "aaaaaeeeeiiiiooooouuuunc",
)


def _fold(text: str) -> str:
    """Minúsculas sin diacríticos, conservando la longitud (índices)."""
    return text.lower().translate(FOLD)


def highlight_spans(
    text: str, tokens: Iterable[str]
) -> list[tuple[int, int]]:
    """Rangos ``(inicio, fin)`` de las coincidencias, sin solapamientos.

    La comparación ignora mayúsculas y diacríticos, igual que el tokenizador
    FTS (``remove_diacritics 2``), y mantiene los índices del texto original.
    """
    if not text or not tokens:
        return []
    lower = _fold(text)
    spans: list[tuple[int, int]] = []
    for token in dict.fromkeys(_fold(token) for token in tokens if token):
        start = 0
        while True:
            index = lower.find(token, start)
            if index < 0:
                break
            spans.append((index, index + len(token)))
            start = index + len(token)
    spans.sort()
    merged: list[tuple[int, int]] = []
    for start, end in spans:
        if merged and start <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
        else:
            merged.append((start, end))
    return merged


def short_ts(value: str | None) -> str:
    """``2026-10-31T12:53:38-05:00`` → ``2026-10-31 12:53``."""
    if not value:
        return "-"
    text = str(value)
    if "T" in text:
        date, _, rest = text.partition("T")
        return f"{date} {rest[:5]}"
    return text[:16]


def _field(label: str, value: str, style: str = "value", width: int = 0) -> Line:
    text = f"{label:<12}{value}"
    if width:
        text = text[:width]
    return Line(text, style)


def detail_lines(
    ticket: dict[str, Any] | None,
    width: int,
    session_id: str | None = None,
    session_dir: str | None = None,
) -> list[Line]:
    """Construye el panel de detalle de un ticket."""
    if ticket is None:
        return [Line("Sin ticket seleccionado.", "dim")]

    inner = max(16, width - 1)
    lines: list[Line] = []
    head = f"{ticket['code']}  {ticket['title']}"
    for index, chunk in enumerate(wrap(head, inner, max_lines=3)):
        lines.append(Line(chunk, "title" if index == 0 else "dim"))
    lines.append(Line("─" * inner, "dim"))

    status = ticket["status"]
    priority = ticket["priority"]
    lines.append(_field("Estado", status, "status", inner))
    lines.append(_field("Prioridad", priority, "priority", inner))
    lines.append(_field("Tipo", ticket["kind"], "value", inner))
    lines.append(_field("Proyecto", ticket.get("project") or "-", "value", inner))

    agent = agents.agent_name(ticket) or "-"
    session = "sesión viva" if session_id else "sin sesión"
    lines.append(_field("Agente", f"{agent} ({session})", "accent", inner))
    if session_id:
        directory = session_dir or "(no registrado)"
        try:
            missing = bool(session_dir) and not Path(session_dir).expanduser().is_dir()
        except (OSError, ValueError):
            missing = bool(session_dir)
        style = "dim" if missing or not session_dir else "value"
        for index, chunk in enumerate(
            wrap(f"{'Directorio':<12}{directory}", inner, max_lines=3)
        ):
            lines.append(Line(chunk, style if index == 0 else "dim"))
        if missing:
            lines.append(
                Line("             el directorio ya no existe; se abrirá en el del ticket", "dim")
            )
    progress = ticket.get("progress") or {}
    if progress.get("total"):
        total = progress["total"]
        confirmed = progress.get("confirmed", 0)
        tasks = "tarea" if total == 1 else "tareas"
        label = "confirmada" if confirmed == 1 else "confirmadas"
        lines.append(
            _field(
                "Progreso",
                f"{progress['done']}/{total} {tasks} · {confirmed} {label}",
                "value",
                inner,
            )
        )

    iterations = int(ticket.get("iterations") or 0)
    if iterations:
        lines.append(_field("Iteraciones", str(iterations), "value", inner))

    commits = ticket.get("commits") or []
    if commits:
        value = commits[0][:12]
        if len(commits) > 1:
            value += f" +{len(commits) - 1}"
        lines.append(_field("Commit", value, "value", inner))
    elif ticket.get("status") in ("completado", "verificado", "confirmado"):
        lines.append(_field("Commit", "sin commit", "dim", inner))

    lines.append(_field("Creado", short_ts(ticket.get("created_at")), "dim", inner))
    lines.append(
        _field("Actualizado", short_ts(ticket.get("updated_at")), "dim", inner)
    )
    if ticket.get("started_at"):
        lines.append(
            _field("Iniciado", short_ts(ticket.get("started_at")), "dim", inner)
        )
    if ticket.get("completed_at"):
        lines.append(
            _field("Completado", short_ts(ticket.get("completed_at")), "dim", inner)
        )

    tags = ticket.get("tags") or []
    if tags:
        lines.append(Line(""))
        lines.append(Line("Etiquetas", "label"))
        for chunk in wrap(" · ".join(tags), inner - 2, max_lines=2):
            lines.append(Line("  " + chunk, "accent"))

    body = (ticket.get("body") or "").strip()
    lines.append(Line(""))
    lines.append(Line("Descripción", "label"))
    if body:
        for chunk in wrap(body, inner - 2, max_lines=8):
            lines.append(Line("  " + chunk))
    else:
        lines.append(Line("  (sin descripción)", "dim"))

    resolution = (ticket.get("resolution") or "").strip()
    if resolution:
        lines.append(Line(""))
        lines.append(Line("Resolución", "label"))
        for chunk in wrap(resolution, inner - 2, max_lines=5):
            lines.append(Line("  " + chunk))

    subtasks = ticket.get("subtasks") or []
    if subtasks:
        lines.append(Line(""))
        lines.append(Line(f"Subtareas ({len(subtasks)})", "label"))
        for sub in subtasks[:6]:
            mark = (
                "✓"
                if sub["status"] in ("completado", "verificado", "confirmado")
                else "·"
            )
            text = f"  {mark} {sub['code']}  {sub['status']:<11} {sub['title']}"
            lines.append(Line(text[:inner], "dim" if mark == "·" else "value"))
        if len(subtasks) > 6:
            lines.append(Line(f"  … {len(subtasks) - 6} tarea(s) más", "dim"))

    comments = ticket.get("comments") or []
    if comments:
        lines.append(Line(""))
        lines.append(Line(f"Comentarios ({len(comments)})", "label"))
        for comment in comments[-3:]:
            head = f"  {short_ts(comment.get('ts'))[11:]} {comment.get('author')}: "
            wrapped = wrap(comment.get("body") or "", max(8, inner - len(head)), 2)
            lines.append(Line((head + wrapped[0])[:inner], "value"))
            for extra in wrapped[1:]:
                lines.append(Line((" " * len(head) + extra)[:inner], "dim"))

    events = ticket.get("events") or []
    if events:
        lines.append(Line(""))
        lines.append(Line("Historial", "label"))
        for event in events[-6:]:
            when = short_ts(event.get("ts"))[11:]
            action = event.get("action") or ""
            actor = event.get("actor") or ""
            target = event.get("to_status")
            changed = target and target != event.get("from_status")
            detail = f" → {target}" if changed else ""
            text = f"  {when} {action} {actor}{detail}"
            lines.append(Line(text[:inner], "dim"))

    links = ticket.get("links") or []
    if links:
        lines.append(Line(""))
        lines.append(Line(f"Enlaces ({len(links)})", "label"))
        for link in links[-6:]:
            text = f"  {link.get('kind')}: {link.get('ref')}"
            lines.append(Line(text[:inner], "accent"))

    parent = ticket.get("parent_code")
    if parent:
        lines.append(Line(""))
        lines.append(Line(f"Padre: {parent}", "dim"))

    return lines


def activity_lines(activity: Iterable[dict[str, Any]], width: int) -> list[Line]:
    """Construye el feed de actividad global."""
    inner = max(16, width - 1)
    lines: list[Line] = []
    for event in activity:
        when = short_ts(event.get("ts"))[11:]
        code = event.get("code") or "?"
        action = event.get("action") or ""
        actor = event.get("actor") or ""
        target = event.get("to_status")
        arrow = f" → {target}" if target and target != event.get("from_status") else ""
        head = f"{when}  {code}  {action}{arrow}"
        lines.append(Line(head[:inner], "accent"))
        note = actor + (f" · {event['note']}" if event.get("note") else "")
        for chunk in wrap(note, inner - 2, max_lines=1):
            lines.append(Line("  " + chunk, "dim"))
    if not lines:
        lines.append(Line("Sin actividad reciente.", "dim"))
    return lines
