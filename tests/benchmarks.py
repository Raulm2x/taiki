"""Benchmarks de escalabilidad y línea base de contexto.

Primero mide connect/export con 20k tickets/eventos. Desde T-0058.06 imprime
además una foto reproducible del coste de contexto: tamaño del catálogo
``tools/list``, arranque en frío del CLI y uso del brief. Todo es local, sin
red y sin esperas fijas; el tiempo usa ``perf_counter`` y medianas.
"""

from __future__ import annotations

import argparse
import json
import os
import statistics
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from ticketlib import db, repo
from ticketlib.memory.config import DEFAULT_MEMORY_CONFIG

TICKETS = 20_000
BODY = "x" * 1024
CONNECT_LIMIT_MS = 10.0
EXPORT_LIMIT_MS = 500.0
CI_CONNECT_LIMIT_MS = 25.0
CI_EXPORT_LIMIT_MS = 1_500.0

ROOT = Path(__file__).resolve().parent.parent
TOOLS_LIMIT_BYTES = 32 * 1024
STARTUP_TARGET_MS = 300.0
STARTUP_RUNS = 3
BRIEF_PATH = ROOT / ".taiki" / "generated" / "brief.md"


def _truthy(value: object) -> bool:
    return str(value or "").strip().lower() in ("1", "true", "yes", "on")


def limits(environ: dict[str, str] | None = None) -> tuple[float, float]:
    """Límites efectivos: estrictos en local, holgados en CI.

    ``TAIKI_BENCH_CONNECT_MS`` / ``TAIKI_BENCH_EXPORT_MS`` permiten ajustar un
    runner concreto sin tocar el código.
    """
    env = os.environ if environ is None else environ
    if _truthy(env.get("CI")) or _truthy(env.get("TAIKI_CI")):
        connect, export = CI_CONNECT_LIMIT_MS, CI_EXPORT_LIMIT_MS
    else:
        connect, export = CONNECT_LIMIT_MS, EXPORT_LIMIT_MS
    for name, key in (
        ("TAIKI_BENCH_CONNECT_MS", "connect"),
        ("TAIKI_BENCH_EXPORT_MS", "export"),
    ):
        raw = env.get(name)
        if raw is None or not str(raw).strip():
            continue
        try:
            value = float(raw)
        except ValueError:
            continue
        if key == "connect":
            connect = value
        else:
            export = value
    return connect, export


def build(path: Path) -> None:
    path.unlink(missing_ok=True)
    for suffix in ("-wal", "-shm"):
        Path(str(path) + suffix).unlink(missing_ok=True)
    conn = db.connect(path)
    rows = [
        (
            f"T-{index:04d}",
            f"Ticket sintético {index}",
            BODY,
            "2026-01-01T00:00:00-05:00",
            "2026-01-02T00:00:00-05:00",
        )
        for index in range(1, TICKETS + 1)
    ]
    with db.tx(conn):
        conn.executemany(
            "INSERT INTO tickets(code, title, body, status, priority, "
            "created_by, created_at, updated_at) "
            "VALUES (?, ?, ?, 'nuevo', 'media', 'bench', ?, ?)",
            rows,
        )
        conn.executemany(
            "INSERT INTO events(ticket_id, ts, actor, action, note) "
            "VALUES (?, '2026-01-01T00:00:00-05:00', 'bench', 'crear', '')",
            [(index,) for index in range(1, TICKETS + 1)],
        )
    conn.close()


def measure_connect(path: Path, runs: int = 5) -> float:
    samples = []
    for _ in range(runs):
        started = time.perf_counter()
        conn = db.connect(path)
        conn.execute("SELECT COUNT(*) FROM tickets").fetchone()
        conn.close()
        samples.append((time.perf_counter() - started) * 1000)
    return statistics.median(samples)


def measure_export(path: Path) -> float:
    conn = db.connect(path)
    try:
        started = time.perf_counter()
        data = repo.export_all(conn)
        elapsed = (time.perf_counter() - started) * 1000
        if len(data["tickets"]) != TICKETS:
            raise SystemExit(
                f"export devolvió {len(data['tickets'])} tickets, "
                f"esperaba {TICKETS}"
            )
        return elapsed
    finally:
        conn.close()


def tool_catalog() -> tuple[int, int]:
    """Número de tools y bytes del JSON compacto de ``tools/list``."""
    from mcp_layer.schemas import TOOLS

    payload = json.dumps(TOOLS, ensure_ascii=False, separators=(",", ":"))
    return len(TOOLS), len(payload.encode("utf-8"))


def measure_cli_startup(db_file: Path, runs: int = STARTUP_RUNS) -> float:
    """Mediana en ms de ``python3 cli.py list`` contra una DB temporal.

    ``list`` necesita DB, así que el benchmark crea una vacía en su propio
    directorio temporal: el arranque no depende de la DB del proyecto ni de
    sus datos.
    """
    cli = ROOT / "cli.py"
    env = dict(os.environ)
    env["TAIKI_DB"] = str(db_file)
    samples = []
    for _ in range(runs):
        started = time.perf_counter()
        completed = subprocess.run(
            [sys.executable, str(cli), "list", "--limit", "1"],
            cwd=ROOT,
            env=env,
            capture_output=True,
            text=True,
        )
        if completed.returncode != 0:
            raise SystemExit(
                "cli.py list falló en el benchmark: "
                f"{(completed.stderr or completed.stdout).strip()}"
            )
        samples.append((time.perf_counter() - started) * 1000)
    return statistics.median(samples)


def brief_usage(path: Path = BRIEF_PATH) -> tuple[int, int] | None:
    """Chars del brief generado y su presupuesto, o ``None`` si no existe."""
    if not path.exists():
        return None
    budget = int(DEFAULT_MEMORY_CONFIG["brief_budget_chars"])
    config = ROOT / ".taiki" / "config.json"
    if config.exists():
        try:
            data = json.loads(config.read_text(encoding="utf-8"))
            budget = int(data.get("memory", {}).get("brief_budget_chars", budget))
        except (OSError, ValueError, TypeError):
            pass
    return len(path.read_text(encoding="utf-8")), budget


def report_context(db_file: Path, brief: Path = BRIEF_PATH) -> bool:
    """Imprime la línea base de contexto; ``False`` si algo sale del objetivo.

    Los umbrales son de decisión (crear un follow-up con datos), no un gate:
    el tiempo depende de la máquina y el brief del estado de la memoria.
    """
    count, size = tool_catalog()
    startup_ms = measure_cli_startup(db_file)
    usage = brief_usage(brief)

    within = size <= TOOLS_LIMIT_BYTES and startup_ms <= STARTUP_TARGET_MS
    print(
        f"tools/list:  {count} tools, {size} bytes "
        f"({size / 1024:.1f} KiB, objetivo {TOOLS_LIMIT_BYTES // 1024} KiB)"
    )
    print(
        f"CLI arranque: {startup_ms:.0f} ms (mediana de {STARTUP_RUNS}, "
        f"objetivo {STARTUP_TARGET_MS:.0f} ms)"
    )
    if usage is None:
        print("brief:       sin generar (.taiki/generated/brief.md)")
    else:
        chars, budget = usage
        within = within and chars <= budget
        print(
            f"brief:       {chars} chars "
            f"({100 * chars / budget:.0f}% de {budget})"
        )
    if not within:
        print("AVISO: línea base fuera de objetivo; decide con datos, no optimices a ciegas")
    return within


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Benchmarks de Taiki")
    parser.add_argument(
        "--keep",
        action="store_true",
        help="conserva la DB sintética para inspeccionarla",
    )
    args = parser.parse_args(argv)
    connect_limit, export_limit = limits()
    with tempfile.TemporaryDirectory(prefix="taiki-bench-") as tmp:
        path = Path(tmp) / "bench.db"
        build(path)
        connect_ms = measure_connect(path)
        export_ms = measure_export(path)
        print(
            f"connect 20k: {connect_ms:.1f} ms "
            f"(límite {connect_limit:.0f} ms)"
        )
        print(
            f"export 20k:  {export_ms:.0f} ms "
            f"(límite {export_limit:.0f} ms)"
        )
        context_db = Path(tmp) / "context.db"
        db.connect(context_db).close()
        report_context(context_db)
        if args.keep:
            kept = Path("/tmp/opencode/taiki-bench.db")
            kept.parent.mkdir(parents=True, exist_ok=True)
            path.replace(kept)
            print(f"DB conservada en {kept}")
        failed = connect_ms > connect_limit or export_ms > export_limit
        if failed:
            print("FALLO: benchmark fuera de umbral")
            return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
