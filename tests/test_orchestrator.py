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
        and _rule(conductor, "subagent", "worker") is None
        and _rule(conductor, "subagent", "worker-low") == "allow"
        and _rule(conductor, "subagent", "worker-medium") == "allow"
        and _rule(conductor, "subagent", "worker-high") == "allow"
        and _rule(conductor, "subagent", "reviewer") == "allow",
        "el conductor no edita y solo lanza perfiles worker/reviewer",
    )
    h.ok(
        _rule(conductor, "shell", "taiki confirm*") == "deny"
        and _rule(conductor, "shell", "git merge*") == "deny"
        and _rule(conductor, "shell", "taiki worktree merge*") == "allow"
        and _rule(conductor, "shell", "git push*") == "deny",
        "el conductor mergea tras la confirmación pero no confirma ni publica",
    )
    h.ok(
        "verificado" in conductor_body
        and "Nunca" in conductor_body
        and "tests/run.py" in conductor_body
        and "Borrado seguro" in conductor_body
        and "autoriza" in conductor_body,
        "el prompt del conductor exige gates, verificación y autorizar borrados",
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
        and _rule(worker, "shell", "taiki worktree merge*") == "deny"
        and "Borrado seguro" in worker_body
        and "OK al conductor" in worker_body,
        "el worker no publica, no mergea, no confirma y pide OK para borrar",
    )

    h.ok(
        reviewer["mode"] == "subagent"
        and _rule(reviewer, "edit", "*") == "deny"
        and _rule(reviewer, "shell", "*") == "deny"
        and _rule(reviewer, "shell", "python3 tests/*") == "allow"
        and "hallazgos" in reviewer_body.lower()
        and "No editas" in reviewer_body
        and "No borras" in reviewer_body,
        "el reviewer es de solo lectura, ejecuta tests, hallazgos y no borra",
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

    # ── Checkpoints, leases y reintentos ────────────────────────────────
    from mcp_layer import handlers
    from ticketlib import orchestrator

    task = repo.create_ticket(conn, title="Con checkpoints")
    repo.claim_ticket(
        conn, task["code"], agent="worker", session_id="ses_cp"
    )
    progress = orchestrator.checkpoint(
        conn,
        task["code"],
        "parser implementado",
        pending="tests del parser",
        agent="worker",
        session_id="ses_cp",
    )
    h.ok(
        progress["status"] == "en_progreso"
        and progress["renewed"]
        and progress["lease_until"],
        "un checkpoint renueva el lease del worker",
    )
    h.ok(
        any(
            "checkpoint: parser implementado" in comment["body"]
            and "tests del parser" in comment["body"]
            for comment in repo.get_ticket(conn, task["code"])["comments"]
        ),
        "el checkpoint deja qué se hizo y qué falta",
    )
    h.raises(
        domain.ValidationError,
        lambda: orchestrator.checkpoint(conn, task["code"], "   "),
        "un checkpoint sin nota falla",
    )

    repo.complete_ticket(
        conn,
        task["code"],
        resolution="entrega uno",
        actor="worker",
        session_id="ses_cp",
    )
    first_retry = orchestrator.retry(
        conn,
        task["code"],
        "test del parser en rojo",
        agent="worker",
        session_id="ses_cp",
        limit=3,
    )
    h.ok(
        first_retry["status"] == "en_progreso"
        and first_retry["attempts"] == 2
        and first_retry["session"] == "ses_cp",
        "un fallo reintenta en la misma sesión y suma un intento",
    )
    h.ok(
        repo.get_ticket(conn, task["code"])["owner_session"] == "ses_cp",
        "el reintento no duplica el claim",
    )
    repo.complete_ticket(
        conn,
        task["code"],
        resolution="entrega dos",
        actor="worker",
        session_id="ses_cp",
    )
    blocked_retry = orchestrator.retry(
        conn,
        task["code"],
        "sigue fallando",
        agent="worker",
        session_id="ses_cp",
        limit=2,
    )
    h.ok(
        blocked_retry["blocked"]
        and repo.get_ticket(conn, task["code"])["status"] == "bloqueado",
        "agotar los intentos bloquea para decisión humana",
    )
    h.raises(
        domain.TicketError,
        lambda: orchestrator.retry(conn, task["code"], "otra vez"),
        "un ticket bloqueado no se reintenta solo",
    )

    stale = repo.create_ticket(conn, title="Worker caído")
    repo.claim_ticket(
        conn, stale["code"], agent="worker", session_id="ses_muerto"
    )
    conn.execute(
        "UPDATE tickets SET lease_until = '2000-01-01T00:00:00+00:00' "
        "WHERE code = ?",
        (stale["code"],),
    )
    conn.commit()
    reassigned = orchestrator.reassign_stale(
        conn, agent="conductor", session_id="ses_nuevo"
    )
    h.ok(
        stale["code"] in reassigned["reassigned"]
        and repo.get_ticket(conn, stale["code"])["owner_session"]
        == "ses_nuevo",
        "un lease caducado se detecta y se reasigna",
    )

    mcp_task = repo.create_ticket(conn, title="Checkpoint MCP")
    repo.claim_ticket(
        conn, mcp_task["code"], agent="worker", session_id="ses_mcp_cp"
    )
    mcp_checkpoint = handlers.call_tool(
        "checkpoint",
        {
            "code": mcp_task["code"],
            "note": "avance MCP",
            "pending": "resto",
        },
        meta={"sessionId": "ses_mcp_cp"},
    )
    h.ok(
        mcp_checkpoint["renewed"]
        and "resto" in mcp_checkpoint["checkpoint"],
        "checkpoint por MCP publica progreso y renueva el lease",
    )

    # ── Verificación determinista y tareas de corrección ────────────────
    from tests import coverage_gate
    from tests import run as suite_run

    class _Done:
        def __init__(self, code=0, out="", err=""):
            self.returncode = code
            self.stdout = out
            self.stderr = err

    ok_gates = suite_run.run_verification(
        runner=lambda *args, **kwargs: _Done(0, "ok"),
        steps=(("suite", ["x"]), ("ruff", ["y"])),
    )
    h.ok(
        suite_run.verification_ok(ok_gates)
        and "suite=ok" in suite_run.verification_evidence(ok_gates)
        and "ruff=ok" in suite_run.verification_evidence(ok_gates),
        "los gates verdes producen evidencia reutilizable",
    )
    bad_gates = suite_run.run_verification(
        runner=lambda *args, **kwargs: _Done(1, "", "E501 línea larga"),
        steps=(("ruff", ["x"]),),
    )
    h.ok(
        not suite_run.verification_ok(bad_gates)
        and bad_gates[0]["output"] == "E501 línea larga",
        "un gate rojo conserva la salida para la nota",
    )
    h.ok(
        len(coverage_gate.COVERAGE_STEPS) == 2
        and "coverage" in coverage_gate.COVERAGE_STEPS[0],
        "el gate de cobertura sabe regenerar el informe",
    )
    coverage_ok, coverage_detail = coverage_gate.run_coverage(
        runner=lambda *args, **kwargs: _Done(0)
    )
    h.ok(
        coverage_ok and coverage_detail == "",
        "coverage --run ejecuta los dos pasos",
    )

    verify_epic = repo.create_ticket(conn, title="Épica verificada")
    good_task = repo.create_ticket(
        conn,
        title="Tarea buena",
        parent_id=verify_epic["code"],
        paths=["src/good.py"],
    )
    repo.claim_ticket(
        conn, good_task["code"], agent="worker", session_id="ses_good"
    )
    repo.complete_ticket(
        conn,
        good_task["code"],
        resolution="lista",
        commit="aaa1111",
        actor="worker",
        session_id="ses_good",
    )
    verified = repo.transition(
        conn,
        good_task["code"],
        "verificado",
        note=f"Evidencia: {suite_run.verification_evidence(ok_gates)}",
        actor="conductor",
    )
    h.ok(
        verified["status"] == "verificado"
        and any(
            "suite=ok" in (event.get("note") or "")
            for event in verified["events"]
        ),
        "un éxito marca verificado con evidencia de gates",
    )

    bad_task = repo.create_ticket(
        conn,
        title="Tarea con lint roto",
        parent_id=verify_epic["code"],
        paths=["src/bad.py"],
    )
    repo.claim_ticket(
        conn, bad_task["code"], agent="worker", session_id="ses_bad"
    )
    repo.complete_ticket(
        conn,
        bad_task["code"],
        resolution="entrega con lint roto",
        commit="bbb2222",
        actor="worker",
        session_id="ses_bad",
    )
    same_scope = orchestrator.retry(
        conn,
        bad_task["code"],
        "ruff: E501 en src/bad.py",
        agent="worker",
        session_id="ses_bad",
    )
    h.ok(
        same_scope["status"] == "en_progreso"
        and same_scope["session"] == "ses_bad",
        "un fallo del mismo alcance reabre y conserva la sesión",
    )

    bug = repo.create_ticket(
        conn,
        title="Bug: el parser no cubre comillas",
        kind="bug",
        parent_id=verify_epic["code"],
        paths=["src/parser.py"],
        assigned_to="worker",
    )
    repo.link_ticket(conn, bug["code"], "relacionado", bad_task["code"])
    repo.comment_ticket(
        conn,
        bug["code"],
        "Evidencia: ruff=fallo en src/parser.py; gates del ticket original rojos.",
        author="conductor",
    )
    bug_detail = repo.get_ticket(conn, bug["code"])
    h.ok(
        bug_detail["parent_code"] == verify_epic["code"]
        and bug_detail["paths"] == ["src/parser.py"]
        and bug_detail["assigned_to"] == "worker"
        and any(
            link["kind"] == "relacionado"
            and link["ref"] == bad_task["code"]
            for link in bug_detail["links"]
        ),
        "un fallo independiente crea una tarea bug enlazada y asignada",
    )
    h.raises(
        domain.PermissionDenied,
        lambda: repo.transition(
            conn, good_task["code"], "confirmado", actor="conductor"
        ),
        "el conductor tampoco confirma al verificar",
    )

    # ── Routing de modelos por variante ─────────────────────────────────
    from ticketlib import agents as agents_lib

    worker_cap = agents_lib.capability("worker")
    docs = agents_lib.route("documentacion", worker_cap)
    bug = agents_lib.route("bug", worker_cap)
    h.ok(
        docs["level"] == "low"
        and docs["variant"] == "low"
        and bug["level"] == "high"
        and bug["variant"] == "high",
        "docs usa low y un bug usa high",
    )
    limited = agents_lib.capability("worker-medium")
    unsupported = agents_lib.route("bug", limited, level="xhigh")
    h.ok(
        unsupported["fallback"] is True
        and unsupported["variant"] is None
        and unsupported["level"] == "xhigh",
        "una variante no soportada cae al default del cliente",
    )
    plan = agents_lib.assign("bug")
    h.ok(
        plan["level"] == "high"
        and plan["variant"] == "high"
        and plan["steps"] == agents_lib.STEPS_BY_LEVEL["high"]
        and plan["agent"] in agents_lib.registry(),
        "assign combina agente, variante y límite de steps",
    )
    h.eq(
        agents_lib.concurrency_budget({"orchestrator": {"max_parallel": 2}}),
        2,
        "el presupuesto de concurrencia se lee de la configuración",
    )
    h.raises(
        domain.ValidationError,
        lambda: agents_lib.registry(
            {"agents": {"worker": {"variants": ["turbo"]}}}
        ),
        "el registro rechaza variantes desconocidas",
    )
    h.raises(
        domain.ValidationError,
        lambda: agents_lib.concurrency_budget(
            {"orchestrator": {"max_parallel": 0}}
        ),
        "el presupuesto de concurrencia se valida",
    )
    mcp_agents = handlers.call_tool("agents", {"kind": "documentacion"})
    h.ok(
        mcp_agents["routing"]["variant"] == "low"
        and mcp_agents["concurrency"] >= 1,
        "agents por MCP expone routing y presupuesto de concurrencia",
    )

    # ── Scheduler de paralelismo seguro ─────────────────────────────────
    from ticketlib import commits as commits_lib

    sched_a = repo.create_ticket(
        conn, title="Sched A", project="sched", priority="alta",
        paths=["src/a.py"],
    )
    sched_b = repo.create_ticket(
        conn, title="Sched B", project="sched", priority="alta",
        paths=["src/b.py"],
    )
    sched_c = repo.create_ticket(
        conn, title="Sched C", project="sched", priority="media",
        paths=["src/a.py"],
    )
    sched_d = repo.create_ticket(
        conn, title="Sched D", project="sched", priority="baja",
        paths=["ticketlib/db.py"],
    )
    sched_e = repo.create_ticket(
        conn, title="Sched E", project="sched", priority="baja"
    )
    plan = orchestrator.parallel_plan(conn, project="sched", limit=3)
    h.eq(
        plan["parallel"],
        [sched_a["code"], sched_b["code"]],
        "dos tareas disjuntas corren en paralelo",
    )
    reasons = {item["code"]: item["reason"] for item in plan["serial"]}
    h.ok(
        "solapa" in reasons.get(sched_c["code"], "")
        and "serial" in reasons.get(sched_d["code"], "")
        and "sin paths" in reasons.get(sched_e["code"], ""),
        "solapes, esquema/store y tareas sin paths se serializan",
    )
    h.ok(
        len(plan["parallel"]) <= plan["limit"]
        and commits_lib.overlaps(
            repo.get_ticket(conn, sched_a["code"])["paths"],
            repo.get_ticket(conn, sched_b["code"])["paths"],
        )
        == [],
        "el límite se respeta y los elegidos no solapan",
    )
    tight = orchestrator.parallel_plan(conn, project="sched", limit=1)
    h.ok(
        tight["parallel"] == [sched_a["code"]]
        and sched_b["code"] in tight["deferred"],
        "el presupuesto limita la ola y difiere el resto",
    )
    mcp_plan = handlers.call_tool(
        "schedule", {"project": "sched", "limit": 3}
    )
    h.ok(
        mcp_plan["parallel"] == plan["parallel"]
        and mcp_plan["requires_worktree"] is True,
        "schedule por MCP devuelve el mismo plan y exige worktree",
    )

    # ── E2E: épica con 3 tareas, paralelismo, bug y confirmación ────────
    from ticketlib import status as status_lib

    e2e_epic = repo.create_ticket(conn, title="Épica e2e", project="e2e")
    e2e_tasks = [
        repo.create_ticket(
            conn,
            title=f"E2E {name}",
            project="e2e",
            parent_id=e2e_epic["code"],
            priority=priority,
            paths=[f"src/e2e_{name.lower()}.py"],
        )
        for name, priority in (("A", "alta"), ("B", "alta"), ("C", "media"))
    ]
    wave = orchestrator.parallel_plan(conn, project="e2e", limit=2)
    h.eq(
        wave["parallel"],
        [e2e_tasks[0]["code"], e2e_tasks[1]["code"]],
        "el e2e lanza dos tareas disjuntas en paralelo",
    )

    for index, task in enumerate(e2e_tasks[:2], start=1):
        repo.claim_ticket(
            conn,
            task["code"],
            agent="worker",
            session_id=f"ses_e2e_{index}",
        )
    for index, task in enumerate(e2e_tasks[:2], start=1):
        repo.complete_ticket(
            conn,
            task["code"],
            resolution=f"{task['code']} lista",
            commit=f"e2e000{index}",
            actor="worker",
            session_id=f"ses_e2e_{index}",
        )
        repo.transition(
            conn,
            task["code"],
            "verificado",
            note="gates verdes",
            actor="conductor",
        )

    third = e2e_tasks[2]
    repo.claim_ticket(
        conn, third["code"], agent="worker", session_id="ses_e2e_3"
    )
    repo.complete_ticket(
        conn,
        third["code"],
        resolution="entrega con fallo",
        commit="e2e0003",
        actor="worker",
        session_id="ses_e2e_3",
    )
    retried = orchestrator.retry(
        conn,
        third["code"],
        "ruff: E501",
        agent="worker",
        session_id="ses_e2e_3",
    )
    h.ok(
        retried["status"] == "en_progreso",
        "el fallo del e2e reabre la tarea en la misma sesión",
    )

    e2e_bug = repo.create_ticket(
        conn,
        title="Bug e2e: caso límite",
        kind="bug",
        project="e2e",
        parent_id=e2e_epic["code"],
        paths=["src/e2e_parser.py"],
        assigned_to="worker",
    )
    repo.link_ticket(conn, e2e_bug["code"], "relacionado", third["code"])
    repo.claim_ticket(
        conn, e2e_bug["code"], agent="worker", session_id="ses_e2e_bug"
    )
    repo.complete_ticket(
        conn,
        e2e_bug["code"],
        resolution="caso cubierto",
        commit="e2e0004",
        actor="worker",
        session_id="ses_e2e_bug",
    )
    repo.transition(
        conn,
        e2e_bug["code"],
        "verificado",
        note="reviewer sin hallazgos",
        actor="conductor",
    )

    repo.complete_ticket(
        conn,
        third["code"],
        resolution="corregida",
        commit="e2e0005",
        actor="worker",
        session_id="ses_e2e_3",
    )
    repo.transition(
        conn,
        third["code"],
        "verificado",
        note="gates verdes",
        actor="conductor",
    )

    metrics = status_lib.snapshot(with_servers=False)["metrics"]
    h.ok(
        metrics["verification_failures"] >= 1
        and metrics["max_parallel_observed"] >= 2
        and metrics["lead_time_hours"] is not None,
        "el e2e deja métricas de fallos, paralelismo y lead time",
    )

    repo.transition(conn, e2e_epic["code"], "en_progreso", actor="conductor")
    repo.transition(
        conn,
        e2e_epic["code"],
        "completado",
        note="todas las tareas verificadas",
        actor="conductor",
    )
    repo.transition(
        conn,
        e2e_epic["code"],
        "verificado",
        note="gates verdes sobre la épica",
        actor="conductor",
    )
    confirmed = repo.confirm_tickets(conn, [e2e_epic["code"]])
    h.ok(
        set(confirmed["confirmed"])
        == {
            e2e_epic["code"],
            *(task["code"] for task in e2e_tasks),
            e2e_bug["code"],
        }
        and repo.get_ticket(conn, e2e_epic["code"])["status"] == "confirmado",
        "el usuario confirma el e2e completo",
    )

    # ── Consistencia estática: agentes lanzables y permisos ─────────────
    agent_files = {
        path.stem for path in AGENTS_DIR.glob("*.md")
    }
    conductor_allowed = {
        item["resource"]
        for item in conductor["permissions"]
        if item.get("action") == "subagent"
        and item.get("effect") == "allow"
    }
    plans = {kind: agents_lib.assign(kind) for kind in domain.KINDS}
    profile_ok = True
    for profile in ("worker-low", "worker-medium", "worker-high"):
        meta, _ = _frontmatter(f"{profile}.md")
        cap = agents_lib.capability(profile)
        expected_model = (
            cap["model"]
            if cap["variant"] is None
            else f"{cap['model']}#{cap['variant']}"
        )
        profile_ok = profile_ok and (
            meta["model"] == expected_model
            and meta["steps"] == cap["steps"]
        )
    h.ok(
        all(
            plan["agent"] in agent_files
            and plan["agent"] in conductor_allowed
            and plan["model"]
            and (plan["variant"] or plan["fallback"])
            and plan["steps"] > 0
            for plan in plans.values()
        )
        and profile_ok,
        "todos los tipos y perfiles son lanzables y declaran modelo/steps",
    )
    medium_plan = plans["mantenimiento"]
    h.ok(
        medium_plan["agent"] == "worker-medium"
        and medium_plan["model"] == "opencode-go/deepseek-v4.1-flash"
        and medium_plan["variant"] is None
        and medium_plan["fallback"] is True,
        "worker-medium usa deepseek por defecto, sin variante medium",
    )
    h.ok(
        _rule(worker, "shell", "git add -A*") == "deny"
        and _rule(worker, "shell", "git add .*") == "deny"
        and _rule(worker, "shell", "git commit -a*") == "deny"
        and _rule(worker, "shell", "git stash*") == "deny"
        and _rule(worker, "shell", "git reset*") == "deny"
        and _rule(worker, "shell", "git clean*") == "deny"
        and _rule(worker, "shell", "git checkout -- *") == "deny"
        and _rule(worker, "shell", "taiki transition*") == "deny",
        "el worker no stagea todo, no commitea -a y no usa transition",
    )

    # ── Scheduler con tareas activas y tope por agente ──────────────────
    active_sched = repo.create_ticket(
        conn, title="Activa sched", project="sched2",
        paths=["src/shared.py"],
    )
    repo.claim_ticket(
        conn, active_sched["code"], agent="worker-medium",
        session_id="ses_active",
    )
    ready_sched = repo.create_ticket(
        conn, title="Lista sched", project="sched2",
        paths=["src/shared.py"],
    )
    plan = orchestrator.parallel_plan(conn, project="sched2", limit=3)
    reasons = {item["code"]: item["reason"] for item in plan["serial"]}
    h.ok(
        plan["parallel"] == []
        and "activa" in reasons[ready_sched["code"]],
        "una tarea activa bloquea el mismo path",
    )

    for index in range(3):
        repo.create_ticket(
            conn, title=f"Bug {index}", project="sched3", kind="bug",
            priority="alta", paths=[f"src/bug{index}.py"],
        )
    high_plan = orchestrator.parallel_plan(conn, project="sched3", limit=3)
    h.ok(
        len(high_plan["parallel"]) == 1
        and len(high_plan["deferred"]) == 2,
        "worker-high limita a un writer simultáneo",
    )

    for index in range(2):
        running = repo.create_ticket(
            conn, title=f"Activa {index}", project="sched4",
            paths=[f"src/act{index}.py"],
        )
        repo.claim_ticket(
            conn, running["code"], agent="worker",
            session_id=f"ses_act{index}",
        )
    for index in range(2):
        repo.create_ticket(
            conn, title=f"Libre {index}", project="sched4",
            priority="alta", paths=[f"src/lib{index}.py"],
        )
    global_plan = orchestrator.parallel_plan(
        conn, project="sched4", limit=3
    )
    h.ok(
        len(global_plan["parallel"]) == 1
        and len(global_plan["deferred"]) == 1,
        "las tareas activas consumen el presupuesto global",
    )

    # ── Atribución real por MCP: agente, actor_type y sesión ────────────
    attr = repo.create_ticket(conn, title="Atribución MCP", project="attr")
    handlers.call_tool(
        "claim",
        {"code": attr["code"], "agent": "worker"},
        meta={"sessionId": "ses_attr"},
    )
    detail = repo.get_ticket(conn, attr["code"])
    claim_event = next(
        event
        for event in detail["events"]
        if event["action"] == "transicion"
        and event["to_status"] == "en_progreso"
    )
    h.ok(
        detail["owner_session"] == "ses_attr"
        and claim_event["actor"] == "worker"
        and claim_event["actor_type"] == "agente",
        "claim MCP conserva agente, actor_type y sesión",
    )
    handlers.call_tool(
        "complete",
        {
            "code": attr["code"],
            "agent": "worker",
            "resolution": "lista por MCP",
        },
        meta={"sessionId": "ses_attr"},
    )
    detail = repo.get_ticket(conn, attr["code"])
    close_event = next(
        event
        for event in detail["events"]
        if event["action"] == "transicion" and event["to_status"] == "completado"
    )
    h.ok(
        close_event["actor"] == "worker"
        and close_event["actor_type"] == "agente",
        "complete MCP atribuye el cierre al worker",
    )
    handlers.call_tool(
        "transition",
        {
            "code": attr["code"],
            "agent": "conductor",
            "status": "verificado",
            "note": "gates verdes",
        },
        meta={"sessionId": "ses_conductor"},
    )
    detail = repo.get_ticket(conn, attr["code"])
    verify_event = next(
        event
        for event in detail["events"]
        if event["action"] == "transicion" and event["to_status"] == "verificado"
    )
    h.ok(
        verify_event["actor"] == "conductor"
        and verify_event["actor_type"] == "agente",
        "transition MCP atribuye la verificación al conductor",
    )
    cp_attr = repo.create_ticket(
        conn, title="Checkpoint con atribución", project="attr"
    )
    handlers.call_tool(
        "claim",
        {"code": cp_attr["code"], "agent": "worker"},
        meta={"sessionId": "ses_cp_attr"},
    )
    handlers.call_tool(
        "checkpoint",
        {"code": cp_attr["code"], "agent": "worker", "note": "avance"},
        meta={"sessionId": "ses_cp_attr"},
    )
    cp_detail = repo.get_ticket(conn, cp_attr["code"])
    h.ok(
        cp_detail["comments"][-1]["author"] == "worker"
        and cp_detail["owner_session"] == "ses_cp_attr",
        "checkpoint MCP conserva autor y sesión",
    )

    # ── Prompts: writes por MCP y perfil recomendado ────────────────────
    h.ok(
        "worker-low" in conductor_body
        and "agents" in conductor_body
        and "`worker` genérico" in conductor_body
        and "worker" not in conductor_allowed,
        "el conductor lanza el perfil recomendado y no el worker genérico",
    )
    h.ok(
        all(
            token not in worker_body
            for token in (
                "taiki claim",
                "taiki complete T-",
                "taiki comment",
                "taiki transition T-",
            )
        )
        and "`claim`" in worker_body
        and "`complete`" in worker_body
        and "`checkpoint`" in worker_body,
        "el worker usa MCP para claim/complete/checkpoint",
    )
    h.ok(
        _rule(worker, "shell", "python3 *") is None
        and _rule(worker, "shell", "python3 -c*") == "deny"
        and _rule(worker, "shell", "python3 tests/*") == "allow",
        "el worker no puede ejecutar python3 -c",
    )
    h.ok(
        "session_move" in worker_body
        and "session_move" in conductor_body
        and "worktree" in worker_body
        and "MCP `health`" in conductor_body
        and "taiki health" not in conductor_body,
        "el worker mueve la sesión al worktree y el conductor usa health MCP",
    )
    h.ok(
        _rule(worker, "shell", "taiki *") == "deny"
        and _rule(worker, "shell", "taiki commit *") == "allow"
        and _rule(worker, "shell", "taiki complete*") is None
        and _rule(conductor, "shell", "taiki *") == "deny"
        and _rule(conductor, "shell", "taiki commit *") is None
        and "--agent worker" in worker_body,
        "los writes de taiki solo van por MCP; el commit se atribuye",
    )
    h.ok(
        "pwd" in worker_body
        and "directory == worktree" in conductor_body
        and "agent/T-XXXX-*" in conductor_body
        and "merge-base --is-ancestor" in conductor_body,
        "el protocolo publica y verifica directorio, rama y commit",
    )
    profiles = {
        name: _frontmatter(name)[0]
        for name in (
            "conductor.md",
            "worker.md",
            "worker-low.md",
            "worker-medium.md",
            "worker-high.md",
            "reviewer.md",
        )
    }
    h.ok(
        all(
            _rule(meta, "shell", "uvx *") == "deny"
            and _rule(meta, "shell", "uvx ruff *") == "allow"
            and _rule(meta, "shell", "uvx mypy *") == "allow"
            for meta in profiles.values()
        ),
        "uvx solo permite ruff y mypy en todos los perfiles",
    )
    inspect_rules = (
        "pwd*",
        "ls*",
        "cat *",
        "head *",
        "tail *",
        "sed -n *",
        "sed *",
        "grep *",
        "rg *",
        "find *",
        "wc *",
        "diff *",
        "file *",
        "stat *",
        "echo *",
        "printf *",
        "md5sum *",
        "sha256sum *",
    )
    h.ok(
        all(
            _rule(meta, "shell", pattern) == "allow"
            and _rule(meta, "shell", "git show*") == "allow"
            for meta in profiles.values()
            for pattern in inspect_rules
        )
        and all(
            _rule(profiles[name], "shell", "mkdir -p docs*") == "allow"
            for name in (
                "worker.md",
                "worker-low.md",
                "worker-medium.md",
                "worker-high.md",
            )
        )
        and _rule(profiles["reviewer.md"], "shell", "mkdir -p docs*") is None
        and all(
            _rule(meta, "shell", pattern) == "deny"
            for meta in profiles.values()
            for pattern in (
                "git push*",
                "git merge*",
                "taiki confirm*",
                "rm -rf*",
                "rm -fr*",
                "rm -r*",
                "sudo *",
            )
        )
        and all(
            _rule(profiles[name], "shell", "rm -f *") == "allow"
            and _rule(profiles[name], "shell", "git rm *") == "allow"
            and all(
                _rule(profiles[name], "shell", pattern) == "deny"
                for pattern in (
                    "rm -f /*",
                    "rm -f ../*",
                    "rm -f ~*",
                    "git rm /*",
                    "git rm ../*",
                )
            )
            for name in (
                "worker.md",
                "worker-low.md",
                "worker-medium.md",
                "worker-high.md",
            )
        )
        and all(
            _rule(profiles[name], "shell", "rm *") == "deny"
            and _rule(profiles[name], "shell", "git rm *") == "deny"
            and _rule(profiles[name], "shell", "rm -f *") is None
            for name in ("conductor.md", "reviewer.md")
        )
        and _rule(profiles["conductor.md"], "shell", "taiki worktree merge*")
        == "allow"
        and all(
            _rule(meta, "shell", "taiki worktree merge*") == "deny"
            for name, meta in profiles.items()
            if name != "conductor.md"
        )
        and all(
            _rule(meta, "shell", "*") == "ask"
            for name, meta in profiles.items()
            if name != "reviewer.md"
        )
        and _rule(profiles["reviewer.md"], "shell", "*") == "deny",
        "permisos de shell: inspección sin prompt, denies críticos, borrado y fallback",
    )
    problems = orchestrator.isolation_problems(
        "T-0001",
        worktree="/wt",
        directory="/main",
        branch="main",
        commit=None,
    )
    h.ok(
        orchestrator.isolation_problems(
            "T-0001",
            worktree="/wt",
            directory="/wt",
            branch="agent/T-0001-x",
            commit="abc",
        )
        == []
        and len(problems) == 3
        and any("directory" in item for item in problems)
        and any("rama" in item for item in problems)
        and any("commit" in item for item in problems),
        "el helper valida el aislamiento y detecta directory/rama/commit malos",
    )
