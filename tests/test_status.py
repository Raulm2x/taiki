"""Estado en vivo de sesiones, claims y worktrees."""

from __future__ import annotations

import argparse
import contextlib
import io
from datetime import datetime, timedelta

from commands.status import cmd_status
from ticketlib import repo, status
from tests.support import Harness


def _past(minutes: int = 5) -> str:
    return (
        datetime.now().astimezone() - timedelta(minutes=minutes)
    ).isoformat(timespec="seconds")


def run(h: Harness) -> None:
    conn = h.conn
    first = repo.create_ticket(conn, title="Sesión A", project="taiki")
    second = repo.create_ticket(conn, title="Sesión B", project="taiki")
    repo.claim_ticket(
        conn, first["code"], agent="opencode", session_id="ses_a"
    )
    repo.claim_ticket(
        conn, second["code"], agent="opencode", session_id="ses_b"
    )
    repo.link_ticket(conn, first["code"], "worktree", "/tmp/wt-a")
    repo.link_ticket(conn, first["code"], "rama", "agent/T-0001-a")

    data = status.snapshot(with_servers=False)
    entries = {item["code"]: item for item in data["sessions"]}
    h.eq(len(entries), 2, "status lista las dos sesiones activas")
    h.ok(data["busy"], "status marca el proyecto como ocupado")
    h.eq(entries[first["code"]]["session"], "ses_a", "status expone la sesión dueña")
    h.eq(
        entries[first["code"]]["worktree"],
        "/tmp/wt-a",
        "status enlaza el worktree del ticket",
    )
    h.eq(
        entries[first["code"]]["branch"],
        "agent/T-0001-a",
        "status enlaza la rama del ticket",
    )
    h.eq(data["stale"], [], "sin leases vencidos no hay stale")

    conn.execute(
        "UPDATE tickets SET heartbeat_at = ?, lease_until = ? WHERE code = ?",
        (_past(), _past(), first["code"]),
    )
    conn.commit()
    data = status.snapshot(with_servers=False)
    h.ok(
        first["code"] in data["stale"] and data["busy"],
        "un lease vencido se marca pero la otra sesión mantiene busy",
    )

    conn.execute(
        "UPDATE tickets SET heartbeat_at = ?, lease_until = ? WHERE code = ?",
        (_past(), _past(), second["code"]),
    )
    conn.commit()
    data = status.snapshot(with_servers=False)
    h.ok(
        set(data["stale"]) == {first["code"], second["code"]}
        and not data["busy"],
        "sin latidos recientes el proyecto deja de estar ocupado",
    )

    text = "\n".join(status.lines(data))
    h.ok(
        first["code"] in text and "Solapamientos" in text,
        "lines resume sesiones y solapamientos",
    )
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        code = cmd_status(
            argparse.Namespace(
                window=600, json=False, sessions=True, no_servers=True
            )
        )
    h.ok(
        code == 0 and first["code"] in out.getvalue(),
        "taiki status --sessions responde por CLI",
    )
    from tui import view

    h.ok(
        hasattr(view, "status_modal")
        and any("Estado de sesiones" in line for line in view.HELP_LINES),
        "la TUI expone el modal de sesiones y su atajo",
    )

    # ── Cola del orquestador y métricas ────────────────────────────────
    verified = repo.create_ticket(conn, title="Verificado", project="taiki")
    repo.claim_ticket(
        conn, verified["code"], agent="worker", session_id="ses_v"
    )
    repo.complete_ticket(
        conn,
        verified["code"],
        resolution="lista",
        actor="worker",
        session_id="ses_v",
    )
    repo.transition(
        conn,
        verified["code"],
        "verificado",
        note="gates verdes",
        actor="conductor",
    )
    blocked = repo.create_ticket(conn, title="Bloqueado", project="taiki")
    repo.transition(conn, blocked["code"], "en_progreso", actor="worker")
    repo.transition(
        conn,
        blocked["code"],
        "bloqueado",
        note="requiere decisión humana",
        actor="worker",
    )
    repo.create_ticket(conn, title="Listo", project="taiki")

    data = status.snapshot(with_servers=False)
    h.ok(
        data["queue"]["verifying"] == 1
        and data["queue"]["blocked"] == 1
        and data["queue"]["ready"] == 1
        and data["queue"]["in_progress"] == 2
        and data["queue"]["pending_confirmation"] == 1,
        "la cola cuenta listos, en curso, verificando, bloqueados y por confirmar",
    )
    h.eq(
        data["worktrees"]["count"],
        1,
        "status cuenta los worktrees enlazados",
    )
    h.ok(
        data["metrics"]["lead_time_hours"] is not None
        and data["metrics"]["max_parallel_observed"] >= 2
        and data["metrics"]["verification_failures"] == 0,
        "las métricas miden lead time, paralelismo y fallos",
    )

    repo.transition(
        conn,
        verified["code"],
        "en_progreso",
        note="revisión: falta un caso",
        actor="conductor",
    )
    failures = status.snapshot(with_servers=False)["metrics"][
        "verification_failures"
    ]
    h.eq(
        failures,
        1,
        "un rechazo desde verificado cuenta como fallo de verificación",
    )
    text = "\n".join(status.lines(status.snapshot(with_servers=False)))
    h.ok(
        "Cola del orquestador" in text
        and "verificando" in text
        and "Métricas" in text
        and "coste/tokens" in text,
        "lines muestra la cola y las métricas",
    )

    # ── Escala del sweep line ───────────────────────────────────────────
    from datetime import datetime as _dt, timedelta as _td
    import time as _time

    base = _dt.now()
    intervals = [(base, base + _td(seconds=1)) for _ in range(2000)]
    started = _time.perf_counter()
    maximum = status._max_overlap(intervals)
    elapsed = _time.perf_counter() - started
    h.ok(
        maximum == 2000 and elapsed < 0.1,
        f"el sweep line escala a 2k intervalos ({elapsed:.3f}s)",
    )

    # ── La cola cierra dependencias al verificado ──────────────────────
    dep_base = repo.create_ticket(conn, title="Base dep", project="taiki")
    dep_child = repo.create_ticket(conn, title="Hija dep", project="taiki")
    repo.link_ticket(conn, dep_child["code"], "depende", dep_base["code"])
    repo.transition(conn, dep_base["code"], "en_progreso", actor="worker")
    blocked_ready = status.snapshot(with_servers=False)["queue"]["ready"]
    repo.complete_ticket(
        conn, dep_base["code"], resolution="lista", actor="worker"
    )
    completed_ready = status.snapshot(with_servers=False)["queue"]["ready"]
    repo.transition(
        conn,
        dep_base["code"],
        "verificado",
        note="gates verdes",
        actor="conductor",
    )
    verified_ready = status.snapshot(with_servers=False)["queue"]["ready"]
    h.ok(
        completed_ready == blocked_ready
        and verified_ready == blocked_ready + 1,
        "la cola no cuenta una dependencia completada como lista",
    )
