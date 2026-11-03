"""Motor determinista de candidatos: agrupación, scoring, salida y API pública.

Las reglas y la detección viven en ``candidates_rules``. Este módulo agrupa las
señales por ``(rule, kind, path_prefix, motivo normalizado)``, las deduplica,
calcula la confianza y devuelve los candidatos ordenados. No llama a un LLM, no
usa la red y **no escribe memoria**: solo describe qué merece revisión y con qué
evidencia.
"""

from __future__ import annotations

import hashlib
import sqlite3
from typing import Any

from ..domain import ValidationError
from .candidates_rules import (
    CANDIDATE_RULES,
    _BASE_CONFIDENCE,
    _config,
    _dedupe_evidence,
    _hotspot_signals,
    _load,
    _routing_signals,
    _short,
    _since,
    _ticket_signals,
    rejection_reason,
    wilson_lower,
)
from .models import default_key, is_negative_rule, normalize_fingerprint


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
