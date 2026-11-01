"""Configuración de los agentes del orquestador y flujo conductor/worker."""

from __future__ import annotations

from pathlib import Path

from ticketlib import domain, repo
from tests.support import Harness

AGENTS_DIR = Path(__file__).resolve().parent.parent / ".opencode" / "agents"


def _scalar(value: str):
    text = value.strip().strip('"').strip("'")
    return int(text) if text.isdigit() else text


def _frontmatter(name: str) -> tuple[dict, str]:
    """Lector mínimo del YAML de los agentes (clave, lista de permisos)."""
    text = (AGENTS_DIR / name).read_text(encoding="utf-8")
    _, _, rest = text.partition("---\n")
    raw, _, body = rest.partition("\n---\n")
    meta: dict = {}
    permissions: list[dict] = []
    current: dict | None = None
    for line in raw.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if stripped.startswith("- "):
            current = {}
            permissions.append(current)
            stripped = stripped[2:]
            key, _, value = stripped.partition(":")
            current[key.strip()] = _scalar(value)
            continue
        if current is not None and line.startswith("  ") and ":" in stripped:
            key, _, value = stripped.partition(":")
            current[key.strip()] = _scalar(value)
            continue
        key, _, value = stripped.partition(":")
        current = None
        meta[key.strip()] = _scalar(value)
    meta["permissions"] = permissions
    return meta, body


def _rule(meta: dict, action: str, resource: str) -> str | None:
    effect = None
    for item in meta["permissions"]:
        if item.get("action") == action and item.get("resource") == resource:
            effect = item.get("effect")
    return effect


def run(h: Harness) -> None:
    conn = h.conn

    # ── Configuración de los tres agentes ───────────────────────────────
    conductor, conductor_body = _frontmatter("conductor.md")
    worker, worker_body = _frontmatter("worker.md")
    reviewer, reviewer_body = _frontmatter("reviewer.md")

    h.ok(
        conductor["mode"] == "primary"
        and isinstance(conductor["steps"], int)
        and conductor["steps"] <= 40,
        "el conductor es primario y tiene límite de pasos",
    )
    h.ok(
        _rule(conductor, "edit", "*") == "deny"
        and _rule(conductor, "subagent", "*") == "deny"
        and _rule(conductor, "subagent", "worker") == "allow"
        and _rule(conductor, "subagent", "reviewer") == "allow",
        "el conductor no edita y solo lanza worker/reviewer",
    )
    h.ok(
        _rule(conductor, "shell", "taiki confirm*") == "deny"
        and _rule(conductor, "shell", "taiki worktree merge*") == "deny"
        and _rule(conductor, "shell", "git push*") == "deny",
        "el conductor no confirma, no mergea y no publica",
    )
    h.ok(
        "verificado" in conductor_body
        and "Nunca" in conductor_body
        and "tests/run.py" in conductor_body,
        "el prompt del conductor exige gates y verificación",
    )

    h.ok(
        worker["mode"] == "subagent"
        and _rule(worker, "edit", "*") == "allow"
        and _rule(worker, "subagent", "*") == "deny",
        "el worker edita pero no lanza subagentes",
    )
    h.ok(
        _rule(worker, "shell", "git push*") == "deny"
        and _rule(worker, "shell", "git merge*") == "deny"
        and _rule(worker, "shell", "taiki confirm*") == "deny"
        and _rule(worker, "shell", "taiki worktree merge*") == "deny",
        "el worker no publica, no mergea ni confirma",
    )
    h.ok(
        "worktree" in worker_body and "heartbeat" not in worker_body,
        "el worker reclama en su worktree",
    )

    h.ok(
        reviewer["mode"] == "subagent"
        and _rule(reviewer, "edit", "*") == "deny"
        and _rule(reviewer, "shell", "*") == "deny"
        and _rule(reviewer, "shell", "python3 tests/*") == "allow",
        "el reviewer es de solo lectura y solo ejecuta tests",
    )
    h.ok(
        "hallazgos" in reviewer_body.lower() and "No editas" in reviewer_body,
        "el reviewer devuelve hallazgos sin editar",
    )

    # ── Flujo: conductor delega, verifica y no confirma ─────────────────
    epic = repo.create_ticket(conn, title="Épica orquestada")
    tasks = [
        repo.create_ticket(
            conn,
            title=f"Tarea {index}",
            parent_id=epic["code"],
            paths=[f"src/mod{index}.py"],
        )
        for index in (1, 2)
    ]

    # El conductor elige la primera tarea lista y lanza un worker.
    for task in tasks:
        repo.claim_ticket(
            conn, task["code"], agent="worker", session_id=f"ses_{task['code']}"
        )
        repo.complete_ticket(
            conn,
            task["code"],
            resolution=f"{task['code']} implementada y probada",
            commit="abc1234",
            actor="worker",
            session_id=f"ses_{task['code']}",
        )
        repo.transition(
            conn,
            task["code"],
            "verificado",
            note="tests/run.py --strict-budget, ruff y mypy verdes",
            actor="conductor",
        )

    h.ok(
        all(
            repo.get_ticket(conn, task["code"])["status"] == "verificado"
            for task in tasks
        ),
        "el conductor deja las tareas verificadas",
    )
    h.raises(
        domain.PermissionDenied,
        lambda: repo.transition(
            conn, tasks[0]["code"], "confirmado", actor="conductor"
        ),
        "el conductor no puede confirmar",
    )

    # La verificación falla en la segunda tarea: se reabre y se corrige.
    reopened = repo.transition(
        conn,
        tasks[1]["code"],
        "en_progreso",
        note="reviewer: falta un caso límite",
        actor="conductor",
    )
    h.ok(
        reopened["iterations"] == 2,
        "un fallo de verificación abre un intento nuevo",
    )
    repo.complete_ticket(
        conn,
        tasks[1]["code"],
        resolution="caso límite cubierto",
        commit="def5678",
        actor="worker",
        session_id=f"ses_{tasks[1]['code']}",
    )
    repo.transition(
        conn,
        tasks[1]["code"],
        "verificado",
        note="reviewer sin hallazgos; gates verdes",
        actor="conductor",
    )

    # El usuario confirma la épica; el conductor nunca lo hace.
    repo.transition(conn, epic["code"], "en_progreso", actor="conductor")
    repo.transition(
        conn,
        epic["code"],
        "completado",
        note="todas las tareas verificadas",
        actor="conductor",
    )
    repo.transition(
        conn,
        epic["code"],
        "verificado",
        note="gates verdes sobre la épica",
        actor="conductor",
    )
    report = repo.confirm_tickets(conn, [epic["code"]])
    h.ok(
        set(report["confirmed"]) == {epic["code"], *(t["code"] for t in tasks)}
        and repo.get_ticket(conn, epic["code"])["status"] == "confirmado",
        "solo el usuario confirma la épica y sus tareas verificadas",
    )
