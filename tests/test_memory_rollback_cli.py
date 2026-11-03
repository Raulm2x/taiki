"""CLI de export/import/rollback: contrato, dry-run y auditoría."""

from __future__ import annotations

import contextlib
import io
import json

import cli
from ticketlib.memory import add_memory, patch_memory
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


def run(h: Harness) -> None:
    conn = h.conn
    memory = add_memory(
        conn,
        title="Rollback CLI",
        content="Contenido original.",
        key="rollback-cli",
        source="user",
    )
    snapshot = h.tmp / "rollback-cli.json"
    exported = _json(["memory", "export", str(snapshot), "--json"])
    h.ok(
        exported["path"] == str(snapshot)
        and exported["checksum"].startswith("sha256:")
        and exported["memories"] == 1,
        "memory export --json devuelve metadatos y checksum",
    )
    imported = _json(
        ["memory", "import", str(snapshot), "--strategy", "new", "--dry-run", "--json"]
    )
    h.ok(
        imported["dry_run"] and len(imported["imported"]) == 1,
        "memory import --dry-run informa del plan",
    )

    patch_memory(conn, memory["code"], content="Contenido cambiado 1.")
    update = conn.execute(
        "SELECT id FROM memory_ledger WHERE memory_id = ? AND action = 'update' "
        "ORDER BY id DESC LIMIT 1",
        (memory["id"],),
    ).fetchone()
    rolled = _json(["memory", "rollback", str(update["id"]), "--json"])
    h.ok(
        rolled["mode"] == "ledger"
        and rolled["code"] == memory["code"]
        and conn.execute(
            "SELECT content FROM memories WHERE code = ?", (memory["code"],)
        ).fetchone()[0]
        == "Contenido original.",
        "memory rollback <ledger-id> restaura y audita",
    )

    patch_memory(conn, memory["code"], content="Contenido cambiado 2.")
    update = conn.execute(
        "SELECT id FROM memory_ledger WHERE memory_id = ? AND action = 'update' "
        "ORDER BY id DESC LIMIT 1",
        (memory["id"],),
    ).fetchone()
    dry = _json(
        ["memory", "rollback", str(update["id"]), "--dry-run", "--json"]
    )
    h.ok(
        dry["dry_run"]
        and conn.execute(
            "SELECT content FROM memories WHERE code = ?", (memory["code"],)
        ).fetchone()[0]
        == "Contenido cambiado 2.",
        "memory rollback --dry-run no escribe",
    )
    _json(["memory", "rollback", str(update["id"]), "--json"])
    h.eq(
        conn.execute(
            "SELECT content FROM memories WHERE code = ?", (memory["code"],)
        ).fetchone()[0],
        "Contenido original.",
        "el rollback real se aplica tras el dry-run",
    )

    patch_memory(conn, memory["code"], content="Contenido cambiado 3.")
    by_snapshot = _json(
        ["memory", "rollback", "--snapshot", str(snapshot), "--json"]
    )
    h.ok(
        by_snapshot["mode"] == "snapshot"
        and conn.execute(
            "SELECT content FROM memories WHERE code = ?", (memory["code"],)
        ).fetchone()[0]
        == "Contenido original.",
        "memory rollback --snapshot restaura el contenido exportado",
    )
    human_code, human, _ = _run(["memory", "rollback", "--snapshot", str(snapshot)])
    h.ok(
        human_code == 0 and "Rollback por snapshot" in human,
        "el rollback por snapshot tiene salida humana",
    )
    invalid_code, _, err = _run(["memory", "rollback", "--json"])
    h.ok(
        invalid_code == 1 and "ledger-id" in err,
        "rollback exige ledger-id o snapshot",
    )
