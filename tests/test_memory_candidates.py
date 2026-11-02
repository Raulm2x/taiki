"""Candidatos deterministas: reglas, filtros, agrupación y umbrales."""

from __future__ import annotations

import json

from ticketlib import db, repo
from ticketlib.domain import ValidationError
from ticketlib.memory import (
    CANDIDATE_RULES,
    generate_candidates,
    rejection_reason,
    validate_memory_config,
    wilson_lower,
)
from tests.support import Harness

_RULES = {item["rule"] for item in CANDIDATE_RULES}


def _ticket(conn, title, *, kind="funcionalidad", paths=None, assigned_to=None):
    return repo.create_ticket(
        conn,
        title=title,
        kind=kind,
        paths=paths or [],
        assigned_to=assigned_to,
    )


def _start(conn, ticket):
    repo.transition(
        conn, ticket["code"], "en_progreso", actor="worker", actor_type="agente"
    )


def _finish(conn, ticket, resolution="hecho"):
    repo.complete_ticket(
        conn, ticket["code"], resolution=resolution, actor="worker", actor_type="agente"
    )


def _verify(conn, ticket):
    repo.transition(
        conn,
        ticket["code"],
        "verificado",
        note="gates ok",
        actor="conductor",
        actor_type="agente",
    )


def _rework(conn, title, why, **kwargs):
    ticket = _ticket(conn, title, **kwargs)
    _start(conn, ticket)
    _finish(conn, ticket)
    _verify(conn, ticket)
    repo.transition(
        conn,
        ticket["code"],
        "en_progreso",
        note=why,
        actor="usuario",
        actor_type="usuario",
    )
    return ticket


def _blocked(conn, title, why, **kwargs):
    ticket = _ticket(conn, title, **kwargs)
    repo.transition(conn, ticket["code"], "pendiente", actor="conductor")
    repo.transition(
        conn,
        ticket["code"],
        "bloqueado",
        note=why,
        actor="conductor",
        actor_type="agente",
    )
    return ticket


def _expensive(conn, title, why, **kwargs):
    ticket = _rework(conn, title, why, **kwargs)
    _finish(conn, ticket)
    _verify(conn, ticket)
    repo.transition(
        conn,
        ticket["code"],
        "en_progreso",
        note=f"{why} otra vez",
        actor="usuario",
        actor_type="usuario",
    )
    return ticket


def _evidence(candidate):
    return {item["ref"] for item in candidate["evidence"]}


def _covers(candidates, rule, code):
    return any(
        candidate["rule"] == rule and code in _evidence(candidate)
        for candidate in candidates
    )


def _rules(h: Harness) -> None:
    conn = db.connect(h.tmp / "candidates.db")
    try:
        normal = _ticket(conn, "Ticket normal", kind="otro", paths=["normal/a.py"])
        _start(conn, normal)
        _finish(conn, normal)
        transient = _rework(
            conn,
            "Rework transitorio",
            "timeout del runner",
            kind="bug",
            paths=["transient/a.py"],
        )
        rework = _rework(
            conn,
            "Rework real",
            "faltó aislar la DB",
            kind="funcionalidad",
            paths=["ticketlib/memory/a.py"],
        )
        rejection = _rework(
            conn,
            "Rechazo real",
            "faltó el caso de error",
            kind="bug",
            paths=["ticketlib/memory/b.py"],
        )
        repo.comment_ticket(
            conn,
            rejection["code"],
            "Rechazado en revisión: Falta el caso de error",
            author="usuario",
            actor_type="usuario",
        )
        blocked = _blocked(
            conn,
            "Bloqueo real",
            "espera decisión humana",
            kind="mantenimiento",
            paths=["ticketlib/memory/c.py"],
        )
        expensive = _expensive(
            conn,
            "Cierre costoso",
            "faltó el índice",
            kind="documentacion",
            paths=["docs/a.md"],
        )
        repo.comment_ticket(
            conn,
            normal["code"],
            "Usa rutas relativas al worktree en los tests",
            author="usuario",
            actor_type="usuario",
        )
        single = _blocked(
            conn, "Bloqueo único", "una sola vez", kind="otro", paths=["single/a.py"]
        )
        first = _ticket(conn, "Prohibición uno", kind="otro")
        second = _ticket(conn, "Prohibición dos", kind="otro")
        for ticket in (first, second):
            repo.comment_ticket(
                conn,
                ticket["code"],
                "Nunca uses git stash",
                author="usuario",
                actor_type="usuario",
            )

        before = conn.execute("SELECT COUNT(*) FROM memories").fetchone()[0]
        candidates = generate_candidates(conn)
        after = conn.execute("SELECT COUNT(*) FROM memories").fetchone()[0]

        rules = {candidate["rule"] for candidate in candidates}
        for rule in ("rework", "rejection", "blocked", "expensive_close", "hotspot", "directive"):
            h.ok(rule in rules, f"la regla {rule} genera candidato")
        h.ok(rules <= _RULES, "ninguna regla desconocida")
        h.eq(before, 0, "el motor no escribe memorias")
        h.eq(after, 0, "el motor sigue sin escribir memorias")

        for ticket in (normal, transient):
            h.ok(
                not any(
                    candidate["rule"] in ("rework", "rejection", "blocked")
                    and _covers([candidate], candidate["rule"], ticket["code"])
                    for candidate in candidates
                ),
                f"{ticket['code']} no genera candidato de incidencia",
            )
        h.ok(
            not _covers(candidates, "rework", rejection["code"]),
            "un rechazo no se duplica como rework",
        )
        h.ok(
            _covers(candidates, "rework", rework["code"]),
            "el rework real tiene evidencia del ticket",
        )
        expensive_candidate = next(
            candidate
            for candidate in candidates
            if candidate["rule"] == "expensive_close"
            and expensive["code"] in _evidence(candidate)
        )
        h.eq(
            expensive_candidate["details"]["iterations"],
            3,
            "el cierre costoso registra los intentos",
        )
        hotspot = next(
            candidate
            for candidate in candidates
            if candidate["rule"] == "hotspot"
            and candidate["details"]["path_prefix"] == "ticketlib/"
        )
        h.ok(hotspot["sample_size"] >= 3, "el hotspot agrupa varios tickets")
        h.ok(
            {rework["code"], rejection["code"], blocked["code"]}
            <= _evidence(hotspot),
            "el hotspot incluye los tickets con incidencia",
        )
        h.ok(
            not any(
                candidate["rule"] == "hotspot"
                and candidate["details"]["path_prefix"] == "single/"
                for candidate in candidates
            ),
            "un solo ticket no forma hotspot",
        )
        directive = next(
            candidate
            for candidate in candidates
            if candidate["rule"] == "directive"
            and normal["code"] in _evidence(candidate)
        )
        h.ok(
            directive["title"].startswith("Directiva de usuario:"),
            "la directiva se titula como tal",
        )
        prohibition = next(
            candidate
            for candidate in candidates
            if candidate["rule"] == "directive"
            and "git stash" in candidate["details"]["motivo"]
        )
        h.eq(prohibition["sample_size"], 2, "la prohibición exige dos evidencias")
        h.eq(
            _evidence(prohibition),
            {first["code"], second["code"]},
            "la prohibición agrupa ambos tickets",
        )

        h.eq(
            [candidate["id"] for candidate in candidates],
            [candidate["id"] for candidate in generate_candidates(conn)],
            "el motor es determinista",
        )
        expected_keys = {
            "id",
            "rule",
            "kind_sugerido",
            "title",
            "summary",
            "evidence",
            "sample_size",
            "confidence",
            "paths",
            "suggested_key",
            "details",
        }
        h.ok(
            all(set(candidate) == expected_keys for candidate in candidates),
            "todos los candidatos exponen el contrato completo",
        )
        h.ok(
            all(0.0 <= candidate["confidence"] <= 1.0 for candidate in candidates),
            "todas las confianzas están en rango",
        )
        h.ok(
            all(len(candidate["id"]) == 16 for candidate in candidates),
            "todos los ids son estables y de 16 caracteres",
        )
        h.ok(
            all(candidate["suggested_key"] for candidate in candidates),
            "todas las claves sugeridas son no vacías",
        )
    finally:
        conn.close()


def _routing(h: Harness) -> None:
    conn = db.connect(h.tmp / "routing.db")
    try:
        for index in range(5):
            ticket = _ticket(
                conn, f"Bug {index}", kind="bug", assigned_to="worker-a"
            )
            _start(conn, ticket)
            _finish(conn, ticket)
        for index in range(2):
            ticket = _ticket(
                conn, f"Bug malo {index}", kind="bug", assigned_to="worker-b"
            )
            _start(conn, ticket)
            _finish(conn, ticket)
            conn.execute(
                "UPDATE tickets SET iterations = 3 WHERE id = ?", (ticket["id"],)
            )

        routing = [
            candidate
            for candidate in generate_candidates(conn)
            if candidate["rule"] == "routing"
        ]
        h.eq(len(routing), 1, "solo el perfil con ventaja genera routing")
        best = routing[0]
        h.eq(best["details"]["agent"], "worker-a", "el routing elige al perfil bueno")
        h.eq(best["sample_size"], 5, "el routing usa la muestra disponible")
        h.eq(best["details"]["successes"], 5, "todos los éxitos cuentan")
        h.ok(best["confidence"] >= 0.5, "el Wilson lower bound supera el umbral")

        for index in range(4):
            ticket = _ticket(
                conn,
                f"Doc {index}",
                kind="documentacion",
                assigned_to="worker-a",
            )
            _start(conn, ticket)
            _finish(conn, ticket)
        ticket = _ticket(
            conn, "Doc mala", kind="documentacion", assigned_to="worker-b"
        )
        _start(conn, ticket)
        _finish(conn, ticket)
        conn.execute(
            "UPDATE tickets SET iterations = 3 WHERE id = ?", (ticket["id"],)
        )
        h.ok(
            not any(
                candidate["rule"] == "routing"
                and candidate["details"]["kind"] == "documentacion"
                for candidate in generate_candidates(conn)
            ),
            "menos de 5 muestras no genera routing",
        )
    finally:
        conn.close()


def _grouping_and_limit(h: Harness) -> None:
    conn = db.connect(h.tmp / "grouping.db")
    try:
        for index in range(3):
            _blocked(
                conn,
                f"Bloqueo {index}",
                "falta decisión de diseño",
                kind="otro",
            )
        _rework(conn, "Rework agrupado", "faltó el test", kind="bug")
        ticket = _ticket(conn, "Directiva agrupada")
        repo.comment_ticket(
            conn,
            ticket["code"],
            "Prefiere el wrapper bin/taiki en los tests",
            author="usuario",
            actor_type="usuario",
        )

        candidates = generate_candidates(conn)
        blocked = [item for item in candidates if item["rule"] == "blocked"]
        h.eq(len(blocked), 1, "el mismo motivo se agrupa en un candidato")
        h.eq(blocked[0]["sample_size"], 3, "la muestra suma los tickets")
        h.eq(len(blocked[0]["evidence"]), 3, "la evidencia no se duplica")

        limited = generate_candidates(conn, {"candidate_max": 2})
        h.eq(len(limited), 2, "candidate_max recorta la lista")
        h.ok(
            limited[0]["confidence"] >= limited[1]["confidence"],
            "los candidatos salen por confianza",
        )
        h.eq(
            len({item["id"] for item in limited}),
            2,
            "no se repiten ids tras recortar",
        )
        h.eq(
            len(generate_candidates(conn, {"candidate_max": 20}, limit=1)),
            1,
            "limit explícito manda sobre la configuración",
        )
        h.raises(
            ValidationError,
            lambda: generate_candidates(conn, {}, limit=0),
            "un límite no positivo falla",
        )
    finally:
        conn.close()


def _thresholds(h: Harness) -> None:
    defaults = validate_memory_config({})
    h.eq(defaults["candidate_routing_min_samples"], 5, "muestras de routing por defecto")
    h.eq(defaults["candidate_max"], 20, "máximo de candidatos por defecto")
    h.eq(defaults["candidate_similarity"], 0.8, "similitud por defecto")
    custom = validate_memory_config(
        {
            "candidate_routing_min_samples": 2,
            "candidate_max": 3,
            "candidate_similarity": 0.5,
        }
    )
    h.eq(custom["candidate_routing_min_samples"], 2, "routing admite otro umbral")
    h.eq(custom["candidate_max"], 3, "máximo admite otro umbral")
    h.eq(custom["candidate_similarity"], 0.5, "similitud admite otro umbral")
    for key, value in (
        ("candidate_routing_min_samples", 0),
        ("candidate_routing_min_samples", "5"),
        ("candidate_max", -1),
        ("candidate_max", True),
        ("candidate_similarity", 0),
        ("candidate_similarity", 1.5),
        ("candidate_similarity", "0.5"),
        ("candidate_similarity", True),
    ):
        h.raises(
            ValidationError,
            lambda key=key, value=value: validate_memory_config({key: value}),
            f"memory.{key}={value!r} se rechaza",
        )
    h.eq(wilson_lower(0, 0), 0.0, "Wilson sin muestra es cero")
    h.ok(
        wilson_lower(5, 5) < 1.0 < 2.0 and wilson_lower(5, 5) > 0.5,
        "Wilson penaliza la muestra pequeña",
    )
    h.eq(
        rejection_reason("Rechazado en revisión: falta probar"),
        "falta probar",
        "el motivo del rechazo se extrae",
    )
    h.eq(rejection_reason("otro comentario"), None, "un comentario normal no es rechazo")
    json.dumps(defaults)


def run(h: Harness) -> None:
    _thresholds(h)
    _rules(h)
    _routing(h)
    _grouping_and_limit(h)
