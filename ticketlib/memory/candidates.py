"""Motor determinista de candidatos de aprendizaje a partir del histórico.

Lee tickets, eventos, comentarios, enlaces, rutas y contadores de intentos y
propone candidatos revisables. No llama a un LLM, no usa la red y **no escribe
memoria**: solo describe qué merece revisión y con qué evidencia.

Reglas:

- ``rework``: tickets con ``iterations >= 2`` o reabiertos desde un estado
  cerrado/verificado.
- ``rejection``: rechazos de revisión con motivo (``Rechazado en revisión:``).
- ``blocked``: tickets que pasaron por ``bloqueado`` o siguen ahí.
- ``expensive_close``: cierres con ``iterations >= 3``.
- ``routing``: pares ``(kind, assigned_to)`` con muestra suficiente y una tasa
  de éxito claramente superior a la media (Wilson lower bound).
- ``hotspot``: prefijos de ruta que acumulan varios tickets con incidencias.
- ``directive``: comentarios explícitos del usuario con una instrucción.

Cada candidato se agrupa por ``(rule, kind, path_prefix, motivo normalizado)``
y se deduplica. Los fallos transitorios, los secretos, las narrativas y las
prohibiciones amplias sin evidencia suficiente se descartan.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
import sqlite3
from datetime import timedelta
from typing import Any

from ..domain import ValidationError
from ..util import now, parse_datetime
from .config import load_memory_config, validate_memory_config
from .models import default_key, is_negative_rule, normalize_fingerprint
from .security import SECRET_PATTERNS

CANDIDATE_RULES: tuple[dict[str, str], ...] = (
    {
        "rule": "rework",
        "summary": "Tickets reabiertos o con más de un intento: hay una "
        "corrección repetible detrás.",
    },
    {
        "rule": "rejection",
        "summary": "Rechazos de revisión con motivo: conviene recordar el "
        "criterio que faltó.",
    },
    {
        "rule": "blocked",
        "summary": "Bloqueos: decisiones o dependencias que conviene "
        "anticipar.",
    },
    {
        "rule": "expensive_close",
        "summary": "Cierres costosos (3+ intentos): el flujo necesita un "
        "atajo o una regla.",
    },
    {
        "rule": "routing",
        "summary": "Routing observado: un perfil resuelve mejor cierto tipo "
        "de ticket.",
    },
    {
        "rule": "hotspot",
        "summary": "Hotspots: rutas que acumulan incidencias entre tickets.",
    },
    {
        "rule": "directive",
        "summary": "Directivas explícitas del usuario que merecen persistir.",
    },
)

_NEGATIVE_RULES = frozenset({"rework", "rejection", "blocked", "expensive_close"})
_BASE_CONFIDENCE: dict[str, float] = {
    "rework": 0.6,
    "rejection": 0.65,
    "blocked": 0.55,
    "expensive_close": 0.7,
    "routing": 0.5,
    "hotspot": 0.5,
    "directive": 0.6,
}
_CLOSED_STATUSES = ("completado", "verificado", "confirmado", "cancelado")
_REOPENED_FROM = ("completado", "verificado", "confirmado")

_TRANSIENT_RE = re.compile(
    r"(?i)\b(timeout|time[- ]out|flaky|intermitente|transitori[oa]|temporal|"
    r"reintent\w*|retry|rate[- ]limit|network|conexi[oó]n|502|503|504)\b"
)
_DIRECTIVE_RE = re.compile(
    r"(?i)\b(usa|usar|prefiere|preferir|siempre|nunca|evita|evitar|recuerda|"
    r"debes|debemos|hay\s+que|no\s+uses|no\s+usar)\b"
)
_REJECTION_RE = re.compile(
    r"^\s*rechazad[oa]\s+en\s+revisi[oó]n\s*[:：]\s*(?P<reason>.*)$",
    re.IGNORECASE,
)


def wilson_lower(successes: int, samples: int, z: float = 1.96) -> float:
    """Límite inferior del intervalo de Wilson para una proporción."""
    if samples <= 0:
        return 0.0
    successes = max(0, min(int(successes), int(samples)))
    proportion = successes / samples
    denominator = 1.0 + (z * z) / samples
    centre = proportion + (z * z) / (2.0 * samples)
    margin = z * math.sqrt(
        (proportion * (1.0 - proportion) + (z * z) / (4.0 * samples)) / samples
    )
    return max(0.0, (centre - margin) / denominator)


def rejection_reason(text: object) -> str | None:
    """Extrae el motivo de un comentario ``Rechazado en revisión: …``."""
    match = _REJECTION_RE.match(str(text or ""))
    if match is None:
        return None
    return match.group("reason").strip() or "sin motivo"


def _config(value: dict[str, Any] | None) -> dict[str, Any]:
    """Acepta el bloque ``memory``, el config completo o nada."""
    if value is None:
        return load_memory_config()
    if "memory" in value:
        return validate_memory_config(value.get("memory"))
    if "candidate_max" in value or "candidate_min_evidence" in value:
        return validate_memory_config(value)
    return load_memory_config(config=value)


def _since(days: int, since: Any) -> str:
    """Normaliza la ventana temporal a una marca ISO comparable."""
    if since:
        parsed = parse_datetime(since)
        if parsed is not None:
            return parsed.isoformat(timespec="seconds")
        return str(since)
    base = parse_datetime(now())
    if base is None:  # pragma: no cover - now() siempre devuelve ISO válido
        return now()
    return (base - timedelta(days=int(days))).isoformat(timespec="seconds")


def _short(text: object, limit: int) -> str:
    """Texto compacto de una línea, sin cambios de formato."""
    value = " ".join(str(text or "").split())
    return value[:limit]


def _has_secret(text: str) -> bool:
    return any(pattern.search(text) for _, pattern in SECRET_PATTERNS)


def _usable(text: str) -> bool:
    """¿El motivo sirve para aprender? Fuera transitorios y secretos."""
    return bool(text.strip()) and not _TRANSIENT_RE.search(text) and not _has_secret(text)


def _paths(ticket: dict[str, Any]) -> list[str]:
    raw = ticket.get("paths")
    if isinstance(raw, list):
        parsed: Any = raw
    elif isinstance(raw, str) and raw.strip():
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            parsed = [raw]
    else:
        parsed = []
    if not isinstance(parsed, list):
        parsed = [parsed]
    out: list[str] = []
    for item in parsed:
        value = str(item or "").strip()
        if value and value not in out:
            out.append(value)
    return out


def _path_prefix(path: str) -> str:
    """Prefijo estable de una ruta: primer segmento de directorio."""
    text = str(path or "").strip().replace("\\", "/").lstrip("./")
    parts = [part for part in text.split("/") if part and part not in (".", "..")]
    if not parts:
        return ""
    if len(parts) == 1:
        return parts[0]
    return parts[0] + "/"


def _dedupe_evidence(items: list[dict[str, str]]) -> list[dict[str, str]]:
    seen: set[tuple[str, str]] = set()
    out: list[dict[str, str]] = []
    for item in items:
        key = (str(item.get("kind") or ""), str(item.get("ref") or ""))
        if not key[1] or key in seen:
            continue
        seen.add(key)
        out.append({"kind": key[0], "ref": key[1]})
    out.sort(key=lambda item: (0 if item["kind"] == "ticket" else 1, item["ref"]))
    return out


def _signal(
    rule: str,
    kind: str,
    motive: str,
    evidence: list[dict[str, str]],
    paths: list[str],
    prefix: str,
    tickets: list[str],
    *,
    confidence: float | None = None,
    details: dict[str, Any] | None = None,
    sample_size: int | None = None,
) -> dict[str, Any]:
    return {
        "rule": rule,
        "kind_sugerido": kind,
        "motive": motive,
        "evidence": _dedupe_evidence(evidence),
        "paths": sorted(set(paths)),
        "path_prefix": prefix,
        "tickets": sorted(set(tickets)),
        "confidence": confidence,
        "details": dict(details or {}),
        "sample_size": sample_size,
    }


def _load(conn: sqlite3.Connection, since: str) -> list[dict[str, Any]]:
    """Carga tickets de la ventana con sus eventos, comentarios y enlaces."""
    tickets = [
        dict(row)
        for row in conn.execute(
            "SELECT * FROM tickets WHERE archived = 0 AND updated_at >= ? "
            "ORDER BY id",
            (since,),
        )
    ]
    index = {int(ticket["id"]): ticket for ticket in tickets}
    for ticket in tickets:
        ticket["_events"] = []
        ticket["_comments"] = []
        ticket["_links"] = []
    if not index:
        return tickets

    event_rows = conn.execute(
        "SELECT e.* FROM events e JOIN tickets t ON t.id = e.ticket_id "
        "WHERE t.archived = 0 AND t.updated_at >= ? ORDER BY e.id",
        (since,),
    )
    for row in event_rows:
        data = dict(row)
        index[int(data["ticket_id"])]["_events"].append(data)
    comment_rows = conn.execute(
        "SELECT c.* FROM comments c JOIN tickets t ON t.id = c.ticket_id "
        "WHERE t.archived = 0 AND t.updated_at >= ? ORDER BY c.id",
        (since,),
    )
    for row in comment_rows:
        data = dict(row)
        index[int(data["ticket_id"])]["_comments"].append(data)
    link_rows = conn.execute(
        "SELECT l.* FROM links l JOIN tickets t ON t.id = l.ticket_id "
        "WHERE t.archived = 0 AND t.updated_at >= ? ORDER BY l.id",
        (since,),
    )
    for row in link_rows:
        data = dict(row)
        index[int(data["ticket_id"])]["_links"].append(data)
    return tickets


def _ticket_signals(
    ticket: dict[str, Any], cfg: dict[str, Any]
) -> list[dict[str, Any]]:
    """Señales locales de un ticket (rework, rechazo, bloqueo, coste, directiva)."""
    code = str(ticket["code"])
    kind = str(ticket.get("kind") or "otro")
    title = _short(ticket.get("title"), 160)
    paths = _paths(ticket)
    prefix = _path_prefix(paths[0]) if paths else ""
    evidence = [{"kind": "ticket", "ref": code}]
    for link in ticket["_links"]:
        if str(link.get("kind")) == "commit":
            evidence.append({"kind": "commit", "ref": str(link.get("ref") or "")})
    events = ticket["_events"]
    comments = ticket["_comments"]
    iterations = int(ticket.get("iterations") or 0)

    reopens = [
        event
        for event in events
        if event.get("action") == "transicion"
        and event.get("from_status") in _REOPENED_FROM
        and event.get("to_status") == "en_progreso"
    ]
    rejection_reasons: list[str] = []
    for comment in comments:
        if str(comment.get("author")) != "usuario":
            continue
        reason = rejection_reason(comment.get("body"))
        if reason is not None:
            rejection_reasons.append(reason)

    signals: list[dict[str, Any]] = []

    if rejection_reasons:
        motive = _short(rejection_reasons[0], 200)
        if _usable(motive):
            signals.append(
                _signal(
                    "rejection",
                    "convention",
                    motive,
                    evidence,
                    paths,
                    prefix,
                    [code],
                    details={"rejections": len(rejection_reasons), "kind": kind},
                )
            )
    elif iterations >= 2 or reopens:
        note = ""
        for event in reversed(reopens):
            note = _short(event.get("note"), 200)
            if note:
                break
        motive = note or title
        if _usable(motive):
            attempts = max(iterations, len(reopens) + 1)
            signals.append(
                _signal(
                    "rework",
                    "pitfall",
                    motive,
                    evidence,
                    paths,
                    prefix,
                    [code],
                    details={"iterations": attempts, "kind": kind},
                )
            )

    blocked_notes: list[str] = []
    for event in events:
        if event.get("to_status") == "bloqueado":
            note = _short(event.get("note"), 200)
            if note:
                blocked_notes.append(note)
    if ticket.get("status") == "bloqueado" or blocked_notes:
        motive = ""
        for note in reversed(blocked_notes):
            if note:
                motive = note
                break
        if not motive and comments:
            motive = _short(comments[-1].get("body"), 200)
        motive = motive or title
        if _usable(motive):
            signals.append(
                _signal(
                    "blocked",
                    "pitfall",
                    motive,
                    evidence,
                    paths,
                    prefix,
                    [code],
                    details={"blocked_events": len(blocked_notes), "kind": kind},
                )
            )

    if iterations >= 3:
        motive = f"{iterations} intentos para cerrar un ticket de tipo {kind}"
        signals.append(
            _signal(
                "expensive_close",
                "workflow",
                motive,
                evidence,
                paths,
                prefix,
                [code],
                details={"iterations": iterations, "kind": kind},
            )
        )

    for comment in comments:
        if str(comment.get("author")) != "usuario":
            continue
        body = _short(comment.get("body"), 600)
        if not body or rejection_reason(body) is not None:
            continue
        if not _DIRECTIVE_RE.search(body):
            continue
        if len(body) > int(cfg["max_entry_chars"]) or _has_secret(body):
            continue
        signals.append(
            _signal(
                "directive",
                "preference",
                body,
                evidence,
                paths,
                prefix,
                [code],
                details={"author": "usuario", "kind": kind},
            )
        )

    return signals


def _hotspot_signals(
    tickets: list[dict[str, Any]],
    signals_by_ticket: dict[str, list[dict[str, Any]]],
    cfg: dict[str, Any],
) -> list[dict[str, Any]]:
    """Agrupa tickets con incidencias por prefijo de ruta."""
    buckets: dict[str, dict[str, Any]] = {}
    for ticket in tickets:
        code = str(ticket["code"])
        signals = signals_by_ticket.get(code, [])
        if not any(signal["rule"] in _NEGATIVE_RULES for signal in signals):
            continue
        paths = _paths(ticket)
        prefixes = {_path_prefix(path) for path in paths}
        for prefix in sorted(item for item in prefixes if item):
            bucket = buckets.setdefault(
                prefix, {"tickets": set(), "paths": set(), "kinds": set()}
            )
            bucket["tickets"].add(code)
            bucket["paths"].update(paths)
            bucket["kinds"].add(str(ticket.get("kind") or "otro"))

    minimum = max(2, int(cfg["candidate_min_evidence"]))
    out: list[dict[str, Any]] = []
    for prefix, bucket in sorted(buckets.items()):
        codes = sorted(bucket["tickets"])
        if len(codes) < minimum:
            continue
        evidence = [{"kind": "ticket", "ref": code} for code in codes]
        out.append(
            _signal(
                "hotspot",
                "pitfall",
                f"Incidencias repetidas bajo {prefix}",
                evidence,
                sorted(bucket["paths"]),
                prefix,
                codes,
                details={
                    "path_prefix": prefix,
                    "tickets": len(codes),
                    "kinds": sorted(bucket["kinds"]),
                },
            )
        )
    return out


def _routing_signals(
    tickets: list[dict[str, Any]], cfg: dict[str, Any]
) -> list[dict[str, Any]]:
    """Routing observado por ``(kind, assigned_to)`` con Wilson lower bound."""
    closed = [
        ticket
        for ticket in tickets
        if str(ticket.get("status")) in _CLOSED_STATUSES
        and str(ticket.get("assigned_to") or "").strip()
    ]
    if not closed:
        return []

    def success(ticket: dict[str, Any]) -> bool:
        return str(ticket.get("status")) != "cancelado" and int(
            ticket.get("iterations") or 0
        ) <= 1

    successes_total = sum(1 for ticket in closed if success(ticket))
    baseline = successes_total / len(closed)
    groups: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for ticket in closed:
        key = (
            str(ticket.get("kind") or "otro"),
            str(ticket.get("assigned_to") or "").strip(),
        )
        groups.setdefault(key, []).append(ticket)

    minimum = int(cfg["candidate_routing_min_samples"])
    out: list[dict[str, Any]] = []
    for (kind, agent), items in sorted(groups.items()):
        samples = len(items)
        if samples < minimum:
            continue
        successes = sum(1 for ticket in items if success(ticket))
        rate = successes / samples
        lower = wilson_lower(successes, samples)
        if rate - baseline < 0.15 or lower < 0.5:
            continue
        codes = sorted(str(ticket["code"]) for ticket in items)
        evidence = [{"kind": "ticket", "ref": code} for code in codes]
        out.append(
            _signal(
                "routing",
                "workflow",
                f"{kind} con {agent}: {successes}/{samples} sin rework",
                evidence,
                [],
                "",
                codes,
                confidence=round(lower, 3),
                sample_size=samples,
                details={
                    "kind": kind,
                    "agent": agent,
                    "samples": samples,
                    "successes": successes,
                    "success_rate": round(rate, 3),
                    "baseline": round(baseline, 3),
                    "wilson_lower": round(lower, 3),
                },
            )
        )
    return out


def _title_for(rule: str, motive: str, prefix: str, details: dict[str, Any]) -> str:
    if rule == "rework":
        return f"Rework: {_short(motive, 70)}"
    if rule == "rejection":
        return f"Rechazo de revisión: {_short(motive, 60)}"
    if rule == "blocked":
        return f"Bloqueo: {_short(motive, 70)}"
    if rule == "expensive_close":
        return f"Cierre costoso: {_short(motive, 60)}"
    if rule == "directive":
        return f"Directiva de usuario: {_short(motive, 60)}"
    if rule == "hotspot":
        return f"Hotspot en {prefix}"
    if rule == "routing":
        return (
            f"Routing observado: {details.get('kind')} → {details.get('agent')}"
        )
    return _short(motive, 70)


def _summary_for(
    rule: str, motive: str, sample: int, codes: list[str], details: dict[str, Any]
) -> str:
    if rule == "routing":
        return (
            f"{details.get('agent')} resolvió {details.get('successes')}/"
            f"{details.get('samples')} tickets de tipo {details.get('kind')} "
            f"sin rework (Wilson {details.get('wilson_lower')})."
        )
    evidence = ", ".join(codes[:5])
    if len(codes) > 5:
        evidence += "…"
    if rule == "directive":
        return f"{sample} comentario(s) de usuario con la misma directiva."
    if rule == "hotspot":
        return (
            f"{sample} tickets con incidencias comparten el prefijo "
            f"{details.get('path_prefix')} ({evidence})."
        )
    if rule == "rejection":
        return f"{sample} rechazo(s) de revisión: {_short(motive, 140)} ({evidence})."
    if rule == "rework":
        return f"{sample} ticket(s) reabiertos o con rework: {_short(motive, 140)} ({evidence})."
    if rule == "blocked":
        return f"{sample} bloqueo(s): {_short(motive, 140)} ({evidence})."
    return f"{sample} cierre(s) costoso(s): {_short(motive, 140)} ({evidence})."


def _finalize(group: dict[str, Any]) -> dict[str, Any]:
    rule = str(group["rule"])
    kind = str(group["kind_sugerido"])
    prefix = str(group["path_prefix"])
    motive = str(group["motive"])
    codes = list(group["tickets"])
    details = dict(group["details"])
    sample_size = int(group["sample_size"] or len(codes) or 1)
    if rule == "routing" and details.get("samples") is not None:
        sample_size = int(details["samples"])
    details.setdefault("motivo", motive)
    details.setdefault("path_prefix", prefix)
    details["occurrences"] = int(group["occurrences"])
    confidence = group.get("confidence")
    if confidence is None:
        base = _BASE_CONFIDENCE.get(rule, 0.5)
        confidence = min(0.95, base + 0.05 * min(max(sample_size - 1, 0), 5))
    confidence = round(float(confidence), 3)

    title = _title_for(rule, motive, prefix, details)
    summary = _summary_for(rule, motive, sample_size, codes, details)
    payload = "|".join(
        (rule, kind, prefix, normalize_fingerprint(motive))
    )
    candidate_id = hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]
    return {
        "id": candidate_id,
        "rule": rule,
        "kind_sugerido": kind,
        "title": title,
        "summary": summary,
        "evidence": _dedupe_evidence(group["evidence"]),
        "sample_size": sample_size,
        "confidence": confidence,
        "paths": sorted(set(group["paths"])),
        "suggested_key": default_key(f"{rule} {kind} {prefix} {motive}")[:80],
        "details": details,
    }


def _group(signals: list[dict[str, Any]]) -> list[dict[str, Any]]:
    groups: dict[tuple[str, str, str, str], dict[str, Any]] = {}
    for signal in signals:
        key = (
            str(signal["rule"]),
            str(signal["kind_sugerido"]),
            str(signal["path_prefix"]),
            normalize_fingerprint(signal["motive"]),
        )
        group = groups.get(key)
        if group is None:
            groups[key] = {
                "rule": signal["rule"],
                "kind_sugerido": signal["kind_sugerido"],
                "path_prefix": signal["path_prefix"],
                "motive": signal["motive"],
                "evidence": list(signal["evidence"]),
                "paths": list(signal["paths"]),
                "tickets": list(signal["tickets"]),
                "confidence": signal["confidence"],
                "details": dict(signal["details"]),
                "sample_size": signal["sample_size"],
                "occurrences": 1,
            }
            continue
        group["evidence"].extend(signal["evidence"])
        group["paths"].extend(signal["paths"])
        group["tickets"].extend(signal["tickets"])
        group["occurrences"] += 1
        for name, value in signal["details"].items():
            if name not in group["details"]:
                group["details"][name] = value
        if signal["confidence"] is not None:
            current = group["confidence"]
            group["confidence"] = (
                signal["confidence"]
                if current is None
                else max(float(current), float(signal["confidence"]))
            )
        if signal["sample_size"] is not None:
            group["sample_size"] = max(
                int(group["sample_size"] or 0), int(signal["sample_size"])
            )
    candidates = [_finalize(group) for group in groups.values()]
    candidates.sort(
        key=lambda item: (
            -float(item["confidence"]),
            -int(item["sample_size"]),
            str(item["rule"]),
            str(item["id"]),
        )
    )
    return candidates


def generate_candidates(
    conn: sqlite3.Connection,
    config: dict[str, Any] | None = None,
    since: Any = None,
    limit: int | None = None,
) -> list[dict[str, Any]]:
    """Genera candidatos deterministas ordenados por confianza.

    ``since`` acota la ventana (por defecto ``candidate_lookback_days``) y
    ``limit`` recorta el resultado (por defecto ``candidate_max``).
    """
    cfg = _config(config)
    maximum = int(limit if limit is not None else cfg["candidate_max"])
    if isinstance(limit, bool) or maximum <= 0:
        raise ValidationError("El límite de candidatos debe ser mayor que 0")

    tickets = _load(conn, _since(int(cfg["candidate_lookback_days"]), since))
    signals: list[dict[str, Any]] = []
    by_ticket: dict[str, list[dict[str, Any]]] = {}
    for ticket in tickets:
        local = _ticket_signals(ticket, cfg)
        by_ticket[str(ticket["code"])] = local
        signals.extend(local)
    signals.extend(_hotspot_signals(tickets, by_ticket, cfg))
    signals.extend(_routing_signals(tickets, cfg))

    candidates = _group(signals)
    minimum = int(cfg["candidate_min_evidence"])
    unique: list[dict[str, Any]] = []
    seen: set[str] = set()
    for candidate in candidates:
        if (
            candidate["rule"] == "directive"
            and int(candidate["sample_size"]) < minimum
            and is_negative_rule(str(candidate["details"].get("motivo") or ""))
        ):
            continue
        key = str(candidate["id"])
        if key in seen:
            continue
        seen.add(key)
        unique.append(candidate)
    return unique[:maximum]


__all__ = [
    "CANDIDATE_RULES",
    "generate_candidates",
    "rejection_reason",
    "wilson_lower",
]
