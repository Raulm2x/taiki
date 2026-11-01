"""Benchmarks de escalabilidad: connect y export con 20k tickets/eventos."""

from __future__ import annotations

import argparse
import statistics
import tempfile
import time
from pathlib import Path

from ticketlib import db, repo

TICKETS = 20_000
BODY = "x" * 1024
CONNECT_LIMIT_MS = 10.0
EXPORT_LIMIT_MS = 500.0


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


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Benchmarks de Taiki")
    parser.add_argument(
        "--keep",
        action="store_true",
        help="conserva la DB sintética para inspeccionarla",
    )
    args = parser.parse_args(argv)
    with tempfile.TemporaryDirectory(prefix="taiki-bench-") as tmp:
        path = Path(tmp) / "bench.db"
        build(path)
        connect_ms = measure_connect(path)
        export_ms = measure_export(path)
        print(
            f"connect 20k: {connect_ms:.1f} ms "
            f"(límite {CONNECT_LIMIT_MS:.0f} ms)"
        )
        print(
            f"export 20k:  {export_ms:.0f} ms "
            f"(límite {EXPORT_LIMIT_MS:.0f} ms)"
        )
        if args.keep:
            kept = Path("/tmp/opencode/taiki-bench.db")
            kept.parent.mkdir(parents=True, exist_ok=True)
            path.replace(kept)
            print(f"DB conservada en {kept}")
        failed = connect_ms > CONNECT_LIMIT_MS or export_ms > EXPORT_LIMIT_MS
        if failed:
            print("FALLO: benchmark fuera de umbral")
            return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
