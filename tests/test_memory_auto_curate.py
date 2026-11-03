"""Auto-curado opt-in y review_command: configuración, intervalo y propuestas."""

from __future__ import annotations

import argparse
import contextlib
import io
import json
import shlex
import sys
from datetime import datetime, timedelta

from commands import status as status_command
from commands import tickets as tickets_command
from ticketlib import db
from ticketlib.domain import ValidationError
from ticketlib.memory import add_memory
from ticketlib.memory.config import validate_memory_config
from ticketlib.memory.curate import AUTO_CURATE_LAST_RUN, auto_curate
from ticketlib.memory.review import run_review_command
from tests.support import Harness

_BASE = {
    "stale_after_days": 10,
    "archive_after_days": 20,
    "candidate_similarity": 0.99,
}


def _ago(days: int) -> str:
    return (
        datetime.now().astimezone() - timedelta(days=days)
    ).isoformat(timespec="seconds")


def _old(conn, code: str, days: int = 30) -> None:
    stamp = _ago(days)
    conn.execute(
        "UPDATE memories SET created_at = ?, updated_at = ?, last_used_at = ? "
        "WHERE code = ?",
        (stamp, stamp, stamp, code),
    )
    conn.commit()


def _command(operations: list[dict]) -> str:
    script = (
        "import json,sys; json.load(sys.stdin); "
        "print(json.dumps({'operations': "
        + json.dumps(operations)
        + "}))"
    )
    return f"{shlex.quote(sys.executable)} -c {shlex.quote(script)}"


def _rejects(h: Harness, cases: list, message: str) -> None:
    rejected = 0
    for case in cases:
        try:
            case()
        except ValidationError:
            rejected += 1
    h.eq(rejected, len(cases), message)


def _disabled(h: Harness) -> None:
    conn = db.connect(h.tmp / "auto-disabled.db")
    try:
        add_memory(
            conn,
            title="No tocar",
            content="Sigue activa.",
            key="auto-disabled",
            source="user",
        )
        cfg = validate_memory_config({**_BASE})
        outcome = auto_curate(conn, cfg)
        forced = auto_curate(conn, cfg, force=True)
        h.ok(
            cfg["auto_curate"]["enabled"] is False
            and outcome["ran"] is False
            and outcome["reason"] == "disabled"
            and forced["ran"] is True
            and forced["reason"] == "forced"
            and db.meta_get(conn, AUTO_CURATE_LAST_RUN, None) is not None,
            "desactivado no ejecuta pero el comando explícito fuerza el curado",
        )
    finally:
        conn.close()


def _interval(h: Harness) -> None:
    conn = db.connect(h.tmp / "auto-interval.db")
    try:
        memory = add_memory(
            conn,
            title="Vieja automática",
            content="Debe caducar.",
            key="auto-interval",
            source="user",
        )
        _old(conn, memory["code"])
        cfg = validate_memory_config(
            {
                **_BASE,
                "auto_curate": {
                    "enabled": True,
                    "interval_hours": 1,
                    "idle_minutes": 0,
                },
            }
        )
        first = auto_curate(conn, cfg)
        status = conn.execute(
            "SELECT status FROM memories WHERE code = ?", (memory["code"],)
        ).fetchone()[0]
        second = auto_curate(conn, cfg)
        h.ok(
            first["ran"]
            and first["result"]["stale"] == [memory["code"]]
            and status == "stale"
            and second["ran"] is False
            and second["reason"] == "interval",
            "auto_curate activo ejecuta el curado y el intervalo impide repetirlo",
        )
    finally:
        conn.close()


def _dry_run(h: Harness) -> None:
    conn = db.connect(h.tmp / "auto-dry.db")
    try:
        memory = add_memory(
            conn,
            title="Dry automática",
            content="No debe cambiar.",
            key="auto-dry",
            source="user",
        )
        _old(conn, memory["code"])
        cfg = validate_memory_config(
            {
                **_BASE,
                "auto_curate": {
                    "enabled": True,
                    "interval_hours": 1,
                    "idle_minutes": 0,
                },
            }
        )
        planned = auto_curate(conn, cfg, dry_run=True)
        status = conn.execute(
            "SELECT status FROM memories WHERE code = ?", (memory["code"],)
        ).fetchone()[0]
        h.ok(
            planned["ran"]
            and planned["result"]["stale"] == [memory["code"]]
            and status == "active"
            and db.meta_get(conn, AUTO_CURATE_LAST_RUN, None) is None,
            "auto_curate dry-run informa sin escribir ni marcar last_run",
        )
    finally:
        conn.close()


def _idle(h: Harness) -> None:
    conn = db.connect(h.tmp / "auto-idle.db")
    try:
        add_memory(
            conn,
            title="Actividad reciente",
            content="Todavía se está usando.",
            key="auto-idle",
            source="user",
        )
        cfg = validate_memory_config(
            {
                **_BASE,
                "auto_curate": {
                    "enabled": True,
                    "interval_hours": 1,
                    "idle_minutes": 60,
                },
            }
        )
        outcome = auto_curate(conn, cfg)
        h.ok(
            outcome["ran"] is False and outcome["reason"] == "idle",
            "idle_minutes evita curar con actividad reciente",
        )
    finally:
        conn.close()


def _review_absent(h: Harness) -> None:
    conn = db.connect(h.tmp / "review-absent.db")
    try:
        report = run_review_command(conn, validate_memory_config({}))
        h.ok(
            report["configured"] is False
            and report["operations"] == 0
            and report["proposed"] == [],
            "sin review_command no hace nada",
        )
    finally:
        conn.close()


def _review_invalid(h: Harness) -> None:
    conn = db.connect(h.tmp / "review-invalid.db")
    try:
        cfg = validate_memory_config(
            {
                "review_command": _command_with_stdout("no es json"),
                "review_timeout_seconds": 5,
            }
        )
        missing = validate_memory_config(
            {
                "review_command": f"{shlex.quote(sys.executable)} -c "
                + shlex.quote("import sys; sys.exit(3)"),
                "review_timeout_seconds": 5,
            }
        )
        _rejects(
            h,
            [
                lambda: run_review_command(conn, cfg),
                lambda: run_review_command(conn, missing),
            ],
            "review_command rechaza JSON inválido y fallo de proceso",
        )
    finally:
        conn.close()


def _command_with_stdout(text: str) -> str:
    return f"{shlex.quote(sys.executable)} -c {shlex.quote(f'print({text!r})')}"


def _review_proposed(h: Harness) -> None:
    conn = db.connect(h.tmp / "review-proposed.db")
    try:
        command = _command(
            [
                {
                    "action": "add",
                    "title": "Propuesta externa",
                    "content": "Regla destilada por el comando.",
                    "kind": "decision",
                    "key": "propuesta-externa",
                    "evidence": [{"kind": "ticket", "ref": "T-0048.05"}],
                }
            ]
        )
        cfg = validate_memory_config(
            {"review_command": command, "review_timeout_seconds": 5}
        )
        before = conn.execute("SELECT COUNT(*) FROM memories").fetchone()[0]
        dry = run_review_command(conn, cfg, dry_run=True)
        h.ok(
            len(dry["proposed"]) == 1
            and conn.execute("SELECT COUNT(*) FROM memories").fetchone()[0] == before,
            "review_command dry-run valida sin crear propuestas",
        )
        report = run_review_command(conn, cfg)
        memory = conn.execute(
            "SELECT * FROM memories WHERE code = ?", (report["proposed"][0],)
        ).fetchone()
        h.ok(
            report["configured"]
            and report["operations"] == 1
            and len(report["proposed"]) == 1
            and memory["status"] == "proposed",
            "review_command deja sus operaciones como propuestas",
        )

        duplicate = _command(
            [
                {
                    "action": "add",
                    "title": "Duplicada externa",
                    "content": "Misma clave.",
                    "kind": "decision",
                    "key": "propuesta-externa",
                }
            ]
        )
        conflict = validate_memory_config(
            {"review_command": duplicate, "review_timeout_seconds": 5}
        )
        h.raises(
            ValidationError,
            lambda: run_review_command(conn, conflict),
            "review_command no reemplaza propuestas existentes",
        )
    finally:
        conn.close()


def _status_with(cfg, path, h: Harness, *, fail: bool = False):
    old_cfg = status_command.load_memory_config
    old_auto = status_command.auto_curate
    status_command.load_memory_config = lambda: cfg
    if fail:
        def boom(*_args, **_kwargs):
            raise RuntimeError("fallo simulado")

        status_command.auto_curate = boom
    out, err = io.StringIO(), io.StringIO()
    try:
        with h.env(TAIKI_DB=str(path)), contextlib.redirect_stdout(
            out
        ), contextlib.redirect_stderr(err):
            code = status_command.cmd_status(
                argparse.Namespace(
                    window=600, json=True, sessions=False, no_servers=True
                )
            )
    finally:
        status_command.load_memory_config = old_cfg
        status_command.auto_curate = old_auto
    return code, out.getvalue(), err.getvalue()


def _next_with(cfg, path, h: Harness, *, fail: bool = False):
    old_cfg = tickets_command.load_memory_config
    old_auto = tickets_command.auto_curate
    tickets_command.load_memory_config = lambda: cfg
    if fail:
        def boom(*_args, **_kwargs):
            raise RuntimeError("fallo simulado")

        tickets_command.auto_curate = boom
    out, err = io.StringIO(), io.StringIO()
    try:
        with h.env(TAIKI_DB=str(path)), contextlib.redirect_stdout(
            out
        ), contextlib.redirect_stderr(err):
            code = tickets_command.cmd_next(
                argparse.Namespace(project=None, json=True)
            )
    finally:
        tickets_command.load_memory_config = old_cfg
        tickets_command.auto_curate = old_auto
    return code, out.getvalue(), err.getvalue()


def _status_next(h: Harness) -> None:
    disabled_path = h.tmp / "status-disabled.db"
    conn = db.connect(disabled_path)
    try:
        memory = add_memory(
            conn,
            title="Status desactivado",
            content="No debe cambiar.",
            key="status-disabled",
            source="user",
        )
        _old(conn, memory["code"])
    finally:
        conn.close()
    disabled_cfg = validate_memory_config(
        {
            **_BASE,
            "auto_curate": {
                "enabled": False,
                "interval_hours": 1,
                "idle_minutes": 0,
            },
        }
    )
    code, _, _ = _status_with(disabled_cfg, disabled_path, h)
    conn = db.connect(disabled_path)
    try:
        status = conn.execute(
            "SELECT status FROM memories WHERE code = ?", (memory["code"],)
        ).fetchone()[0]
        h.ok(
            code == 0
            and status == "active"
            and db.meta_get(conn, AUTO_CURATE_LAST_RUN, None) is None,
            "status desactivado no ejecuta auto-curado",
        )
    finally:
        conn.close()

    enabled_path = h.tmp / "status-enabled.db"
    conn = db.connect(enabled_path)
    try:
        first = add_memory(
            conn,
            title="Status activado",
            content="Debe caducar.",
            key="status-enabled",
            source="user",
        )
        _old(conn, first["code"])
    finally:
        conn.close()
    enabled_cfg = validate_memory_config(
        {
            **_BASE,
            "auto_curate": {
                "enabled": True,
                "interval_hours": 1,
                "idle_minutes": 0,
            },
        }
    )
    code, _, _ = _status_with(enabled_cfg, enabled_path, h)
    conn = db.connect(enabled_path)
    try:
        first_status = conn.execute(
            "SELECT status FROM memories WHERE code = ?", (first["code"],)
        ).fetchone()[0]
        last_run = db.meta_get(conn, AUTO_CURATE_LAST_RUN, None)
        second = add_memory(
            conn,
            title="Status segunda",
            content="No debe caducar todavía.",
            key="status-second",
            source="user",
        )
        _old(conn, second["code"])
    finally:
        conn.close()
    _status_with(enabled_cfg, enabled_path, h)
    conn = db.connect(enabled_path)
    try:
        second_status = conn.execute(
            "SELECT status FROM memories WHERE code = ?", (second["code"],)
        ).fetchone()[0]
        h.ok(
            code == 0
            and first_status == "stale"
            and last_run is not None
            and second_status == "active",
            "status ejecuta el auto-curado vencido y no repite antes del intervalo",
        )
    finally:
        conn.close()

    next_path = h.tmp / "next-enabled.db"
    conn = db.connect(next_path)
    try:
        memory = add_memory(
            conn,
            title="Next activado",
            content="Debe caducar.",
            key="next-enabled",
            source="user",
        )
        _old(conn, memory["code"])
    finally:
        conn.close()
    code, _, _ = _next_with(enabled_cfg, next_path, h)
    conn = db.connect(next_path)
    try:
        h.ok(
            code == 0
            and conn.execute(
                "SELECT status FROM memories WHERE code = ?", (memory["code"],)
            ).fetchone()[0]
            == "stale",
            "next activado ejecuta el auto-curado sin romper la lectura",
        )
    finally:
        conn.close()

    code, out, err = _status_with(enabled_cfg, next_path, h, fail=True)
    status_code, status_out, status_err = code, out, err
    code, out, err = _next_with(enabled_cfg, next_path, h, fail=True)
    h.ok(
        status_code == 0
        and "Aviso" in status_err
        and bool(status_out)
        and code == 0
        and "Aviso" in err,
        "status y next no fallan si el auto-curado lanza",
    )


def run(h: Harness) -> None:
    _disabled(h)
    _interval(h)
    _dry_run(h)
    _idle(h)
    _review_absent(h)
    _review_invalid(h)
    _review_proposed(h)
    _status_next(h)
