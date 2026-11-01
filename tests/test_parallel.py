"""Concurrencia real: dos procesos reclamando el mismo ticket."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from ticketlib import db, repo
from tests.support import Harness

SCRIPT = """
import sys
from ticketlib import db, repo, domain

code, session = sys.argv[1], sys.argv[2]
conn = db.connect()
try:
    repo.claim_ticket(conn, code, agent="opencode", session_id=session)
except domain.TransitionError:
    print("blocked")
else:
    print("ok")
"""


def run(h: Harness) -> None:
    tmp = Path(h.tmp)
    path = tmp / "parallel.db"
    conn = db.connect(path)
    try:
        ticket = repo.create_ticket(conn, title="Claim concurrente", project="taiki")
    finally:
        conn.close()

    script = tmp / "claim.py"
    script.write_text(SCRIPT, encoding="utf-8")
    root = str(Path(__file__).resolve().parent.parent)
    env = {
        **os.environ,
        "TAIKI_DB": str(path),
        "PYTHONPATH": root,
    }
    processes = [
        subprocess.Popen(
            [sys.executable, str(script), ticket["code"], f"ses_{index}"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            env=env,
        )
        for index in (1, 2)
    ]
    results = [process.communicate(timeout=30)[0].strip() for process in processes]
    h.eq(
        sorted(results),
        ["blocked", "ok"],
        "dos procesos reales: solo uno gana el claim",
    )

    conn = db.connect(path)
    try:
        owner = repo.get_ticket(conn, ticket["code"])["owner_session"]
        h.ok(
            owner in {"ses_1", "ses_2"},
            "el dueño del ticket es una de las dos sesiones",
        )
        h.eq(
            repo.get_ticket(conn, ticket["code"])["status"],
            "en_progreso",
            "el claim concurrente deja el ticket en progreso",
        )
    finally:
        conn.close()
