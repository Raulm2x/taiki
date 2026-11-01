"""Inventario y limpieza de servidores MCP."""

from __future__ import annotations

import os
import signal
from mcp_layer import handlers
from ticketlib import db, domain, repo, runtime, servers
from tests.support import Harness


def run(h: Harness) -> None:
    conn = h.conn
    tmp = str(h.tmp)
    ren_epic = repo.create_ticket(conn, title="Épica para el aviso de MCP viejo")

    # ── Inventario y limpieza de servidores MCP ─────────────────
    h.ok(
        servers.is_server("python3 /x/tickets/server.py")
        and servers.is_server("tickets-mcp")
        and not servers.is_server("python3 otra-cosa.py"),
        "se reconoce un servidor MCP por su línea de comandos",
    )
    h.ok(
        servers.classify(
            alive=True,
            looks_like_server=True,
            ppid=88565,
            parent_alive=True,
            fingerprint="abc",
            current="abc",
        )
        == "al día"
        and servers.classify(
            alive=True,
            looks_like_server=True,
            ppid=1,
            parent_alive=False,
            fingerprint="abc",
            current="abc",
        )
        == "huérfano"
        and servers.classify(
            alive=True,
            looks_like_server=True,
            ppid=None,
            parent_alive=False,
            fingerprint=None,
            current="abc",
        )
        == "huérfano"
        and servers.classify(
            alive=True,
            looks_like_server=True,
            ppid=10,
            parent_alive=True,
            fingerprint="viejo",
            current="abc",
        )
        == "desactualizado"
        and servers.classify(
            alive=False,
            looks_like_server=False,
            ppid=None,
            parent_alive=False,
            fingerprint="abc",
            current="abc",
        )
        == "muerto",
        "classify distingue al día, huérfano, desactualizado y muerto",
    )

    procs = {
        100: {"pid": 100, "ppid": 50, "cmdline": "python3 /x/tickets/server.py"},
        200: {"pid": 200, "ppid": 1, "cmdline": "python3 /x/tickets/server.py"},
        50: {"pid": 50, "ppid": 1, "cmdline": "opencode serve --service"},
    }

    def fake_process(pid):
        return procs.get(pid)

    fleet = db.connect(":memory:")
    try:
        for pid, ppid, fingerprint in (
            (100, 50, "abc"),
            (200, 1, "abc"),
            (300, 50, "abc"),
        ):
            fleet.execute(
                "INSERT INTO mcp_servers(pid, ppid, name, fingerprint, "
                "started_at, heartbeat_at) VALUES (?, ?, 'tickets', ?, "
                "'2026-10-31T10:00:00-05:00', '2026-10-31T10:00:00-05:00')",
                (pid, ppid, fingerprint),
            )
        states = {
            item["pid"]: item["state"]
            for item in servers.inventory(
                fleet,
                process=fake_process,
                current="abc",
                live={100: procs[100], 200: procs[200]},
            )
        }
        h.ok(
            states == {100: "al día", 200: "huérfano", 300: "muerto"},
            "el inventario clasifica registro y /proc",
        )

        killed = []

        def fake_killer(pid, sig):
            killed.append((pid, sig))
            procs.pop(pid, None)

        dry = servers.prune(
            fleet,
            apply=False,
            process=fake_process,
            killer=fake_killer,
            current="abc",
            live={100: procs[100], 200: procs[200]},
        )
        h.ok(
            dry["orphans"] == [200]
            and dry["dead"] == [300]
            and killed == [],
            "prune simula sin matar nada",
        )

        done = servers.prune(
            fleet,
            apply=True,
            process=fake_process,
            killer=fake_killer,
            current="abc",
            live={100: procs[100], 200: procs[200]},
            grace=0,
            slug=0,
            sleep=lambda _s: None,
        )
        h.ok(
            done["killed"] == [200]
            and killed
            and killed[0] == (200, signal.SIGTERM),
            "prune solo termina al huérfano y espera su muerte",
        )
        h.ok(
            100 in done["kept"],
            "un servidor con padre vivo nunca se toca (sesión idle)",
        )
        left = {
            row["pid"]
            for row in fleet.execute("SELECT pid FROM mcp_servers")
        }
        h.ok(left == {100}, "prune limpia los registros muertos")

        procs[500] = {
            "pid": 500,
            "ppid": 1,
            "cmdline": "python3 /x/tickets/server.py",
        }
        fleet.execute(
            "INSERT INTO mcp_servers(pid, ppid, name, fingerprint, "
            "started_at, heartbeat_at) VALUES (500, 1, 'tickets', 'abc', "
            "'2026-10-31T10:00:00-05:00', '2026-10-31T10:00:00-05:00')"
        )
        stubborn = servers.prune(
            fleet,
            apply=True,
            process=fake_process,
            killer=lambda pid, sig: None,
            current="abc",
            live={100: procs[100], 500: procs[500]},
            grace=0,
            slug=0,
            sleep=lambda _s: None,
        )
        h.ok(
            stubborn["stubborn"] == [500]
            and fleet.execute(
                "SELECT 1 FROM mcp_servers WHERE pid = 500"
            ).fetchone()
            is not None,
            "si no muere, se conserva la fila para reintentar",
        )

        procs[600] = {
            "pid": 600,
            "ppid": 50,
            "cmdline": "python3 /x/tickets/server.py",
        }
        fleet.execute(
            "INSERT INTO mcp_servers(pid, ppid, name, fingerprint, "
            "started_at, heartbeat_at) VALUES (700, 50, 'tickets', 'abc', "
            "'2026-10-31T10:00:00-05:00', '2026-10-31T10:00:00-05:00')"
        )
        h.ok(
            servers.hygiene(fleet, process=fake_process) == 1
            and fleet.execute(
                "SELECT 1 FROM mcp_servers WHERE pid = 700"
            ).fetchone()
            is None,
            "hygiene borra registros muertos al arrancar",
        )

        procs2 = {
            900: {
                "pid": 900,
                "ppid": 1,
                "cmdline": "opencode serve --service",
            },
            901: {
                "pid": 901,
                "ppid": 900,
                "cmdline": "node /opt/foo-mcp/server.js",
            },
            902: {
                "pid": 902,
                "ppid": 900,
                "cmdline": "/usr/bin/bash -c ls",
            },
            903: {
                "pid": 903,
                "ppid": 1,
                "cmdline": "python3 /x/tickets/server.py",
            },
            904: {
                "pid": 904,
                "ppid": 900,
                "cmdline": "node /opt/mcp-srv/index.js",
            },
        }

        def fake2(pid):
            return procs2.get(pid)

        found = servers.discover(fake2, pids=list(procs2))
        h.ok(
            set(found) == {901, 903, 904}
            and servers.process_name(procs2[901]) == "foo-mcp"
            and servers.is_service(procs2[900]["cmdline"]),
            "descubre otros MCP hijos del servicio y su nombre",
        )

        forged = {
            110: {
                "pid": 110,
                "ppid": 50,
                "cmdline": "python3 /x/tickets/server.py",
            },
            111: {
                "pid": 111,
                "ppid": 50,
                "cmdline": "python3 /x/tickets/server.py",
            },
        }

        def fake3(pid):
            return {**procs, **forged}.get(pid)

        def killer3(pid, sig):
            forged.pop(pid, None)

        twins = servers.inventory(
            db.connect(":memory:"),
            process=fake3,
            current="abc",
            live=forged,
        )
        h.ok(
            [item["state"] for item in twins] == ["duplicado", "duplicado"]
            and [item["duplicate_of"] for item in twins] == [111, 111],
            "varios procesos del mismo servidor se marcan duplicados",
        )

        twins_db = db.connect(":memory:")
        try:
            kept = servers.prune(
                twins_db,
                apply=True,
                process=fake3,
                killer=killer3,
                current="abc",
                live=forged,
                duplicates=False,
                grace=0,
                slug=0,
                sleep=lambda _s: None,
            )
            h.ok(
                kept["killed"] == [] and kept["duplicates"] == [],
                "prune no toca duplicados por defecto",
            )
            pruned = servers.prune(
                twins_db,
                apply=True,
                process=fake3,
                killer=killer3,
                current="abc",
                live=forged,
                duplicates=True,
                grace=0,
                slug=0,
                sleep=lambda _s: None,
            )
            h.ok(
                pruned["killed"] == [110] and 111 in pruned["kept"],
                "con --duplicates termina el viejo y conserva el nuevo",
            )
        finally:
            twins_db.close()

        servers.register(fleet, "tickets", "test")
        row = fleet.execute(
            "SELECT * FROM mcp_servers WHERE pid = ?", (os.getpid(),)
        ).fetchone()
        h.ok(
            row is not None
            and row["fingerprint"] == runtime.code_fingerprint(),
            "el proceso se registra con su huella",
        )
        servers.heartbeat(fleet, connection="ses_x")
        row = fleet.execute(
            "SELECT connection FROM mcp_servers WHERE pid = ?",
            (os.getpid(),),
        ).fetchone()
        h.ok(row["connection"] == "ses_x", "el latido guarda la sesión")

        counts = servers.summary(
            [
                {"state": "al día"},
                {"state": "huérfano"},
                {"state": "huérfano"},
                {"state": "desactualizado"},
                {"state": "muerto"},
            ]
        )
        h.ok(
            counts["total"] == 5
            and counts["orphans"] == 2
            and counts["stale"] == 1
            and counts["dead"] == 1,
            "summary cuenta estados para audit y la TUI",
        )

        fleet.execute("DELETE FROM mcp_servers")
        db.meta_set(fleet, "mcp_pid", "100")
        db.meta_set(fleet, "mcp_fingerprint", "viejo")
        fallback = {
            item["pid"]: item["state"]
            for item in servers.inventory(
                fleet,
                process=fake_process,
                current="abc",
                live={100: procs[100]},
            )
        }
        h.ok(
            fallback == {100: "desactualizado"},
            "sin fila, el handshake de meta identifica el proceso viejo",
        )

        loaded = runtime.LOADED
        runtime.LOADED = "viejo0000000"
        h.ok(
            runtime.is_stale(),
            "runtime detecta que su código quedó viejo",
        )
        runtime.LOADED = loaded
        h.ok(not runtime.is_stale(), "runtime no marca viejo el código actual")

        original_stale = runtime.is_stale
        runtime.is_stale = lambda: True
        try:
            h.raises(
                domain.ValidationError,
                lambda: handlers.call_tool(
                    "create",
                    {
                        "title": "Tarea con MCP viejo",
                        "parent_id": ren_epic["code"],
                    },
                    meta={"sessionId": "ses_mcp_viejo"},
                ),
                "un MCP viejo no crea tareas",
            )
        finally:
            runtime.is_stale = original_stale
    finally:
        fleet.close()
