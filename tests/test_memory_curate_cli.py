"""Contrato del CLI de curado: JSON, dry-run y salida humana."""

from __future__ import annotations

import contextlib
import io
import json
from datetime import datetime, timedelta

import cli
from ticketlib.memory import add_memory
from tests.support import Harness


def _run(argv: list[str]) -> tuple[int, str, str]:
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        try:
            code = cli.main(argv)
        except SystemExit as exc:
            code = int(exc.code or 0)
    return code, out.getvalue(), err.getvalue()


def _json(argv: list[str]) -> dict:
    code, out, err = _run(argv)
    if code != 0:
        raise AssertionError(f"CLI falló ({code}): {err}")
    return json.loads(out)


def _ago(days: int) -> str:
    return (
        datetime.now().astimezone() - timedelta(days=days)
    ).isoformat(timespec="seconds")


def _set_age(conn, code: str, days: int) -> None:
    stamp = _ago(days)
    conn.execute(
        "UPDATE memories SET created_at = ?, updated_at = ?, last_used_at = ? "
        "WHERE code = ?",
        (stamp, stamp, stamp, code),
    )


def run(h: Harness) -> None:
    conn = h.conn
    old = add_memory(
        conn,
        title="Curado CLI stale",
        content="Debe caducar.",
        key="cli-stale",
        source="user",
    )
    stale_old = add_memory(
        conn,
        title="Curado CLI archive",
        content="Debe archivarse.",
        key="cli-archive",
        source="user",
    )
    pinned = add_memory(
        conn,
        title="Curado CLI pin",
        content="No debe tocarse.",
        key="cli-pin",
        source="user",
        pinned=True,
    )
    _set_age(conn, old["code"], 100)
    _set_age(conn, pinned["code"], 200)
    conn.execute(
        "UPDATE memories SET status = 'stale', updated_at = ? WHERE code = ?",
        (_ago(200), stale_old["code"]),
    )
    conn.commit()

    payload = _json(["memory", "curate", "--json"])
    h.eq(
        set(payload),
        {"stale", "archived", "merged", "budget", "report"},
        "el JSON de curate conserva su contrato",
    )
    h.eq(payload["stale"], [old["code"]], "el CLI informa del stale aplicado")
    h.eq(
        payload["archived"],
        [stale_old["code"]],
        "el CLI informa del archivado aplicado",
    )
    h.ok(
        {"project", "user"} <= set(payload["budget"]),
        "el JSON incluye presupuesto por scope",
    )
    statuses = {
        row["code"]: row["status"]
        for row in conn.execute("SELECT code, status FROM memories")
    }
    h.eq(statuses[old["code"]], "stale", "curate aplica el cambio")
    h.eq(statuses[stale_old["code"]], "archived", "curate archiva sin borrar")
    h.eq(statuses[pinned["code"]], "active", "curate respeta pin")

    human_code, human, _ = _run(["memory", "curate"])
    h.ok(
        human_code == 0
        and "Curado de memoria" in human
        and "Presupuesto:" in human,
        "la salida humana resume el curado y el presupuesto",
    )

    dry = add_memory(
        conn,
        title="Curado CLI dry",
        content="Solo debe planificarse.",
        key="cli-dry",
        source="user",
    )
    _set_age(conn, dry["code"], 100)
    conn.commit()
    planned = _json(["memory", "curate", "--dry-run", "--json"])
    h.ok(
        dry["code"] in planned["stale"],
        "dry-run informa del cambio previsto",
    )
    still = conn.execute(
        "SELECT status FROM memories WHERE code = ?", (dry["code"],)
    ).fetchone()[0]
    h.eq(still, "active", "dry-run no escribe el estado")
    dry_human, dry_out, _ = _run(["memory", "curate", "--dry-run"])
    h.ok(
        dry_human == 0 and "(dry-run)" in dry_out,
        "la salida humana marca el dry-run",
    )
