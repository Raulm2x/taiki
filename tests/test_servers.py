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
                pruned["killed"] == []
                and pruned["duplicates"] == []
                and pruned["deferred"] == [110, 111]
                and 110 in pruned["kept"]
                and 111 in pruned["kept"]
                and len(pruned["warnings"]) == 1
                and "--kill ni --force" in pruned["warnings"][0]
                and "110, 111" in pruned["warnings"][0],
                "sin --kill/--force no se termina ningún duplicado vivo, se "
                "listan en deferred y se avisa",
            )

            twins_meta = db.connect(":memory:")
            try:
                db.meta_set(twins_meta, "mcp_pid", "110")
                plain = [
                    {"pid": 110, "connection": None, "heartbeat_at": None},
                    {"pid": 111, "connection": None, "heartbeat_at": None},
                ]
                h.eq(
                    [
                        servers.identify_active(twins_meta, plain),
                        servers.identify_active(
                            twins_meta,
                            [
                                {
                                    "pid": 110,
                                    "connection": None,
                                    "heartbeat_at": None,
                                },
                                {
                                    "pid": 111,
                                    "connection": None,
                                    "heartbeat_at": "nuevo",
                                },
                            ],
                        ),
                        servers.identify_active(
                            twins_db,
                            [
                                {
                                    "pid": 110,
                                    "connection": "ses_a",
                                    "heartbeat_at": None,
                                },
                                {
                                    "pid": 111,
                                    "connection": None,
                                    "heartbeat_at": "nuevo",
                                },
                            ],
                        ),
                        servers.identify_active(
                            twins_db,
                            [
                                {
                                    "pid": 110,
                                    "connection": None,
                                    "heartbeat_at": "10:05",
                                },
                                {
                                    "pid": 111,
                                    "connection": None,
                                    "heartbeat_at": "10:01",
                                },
                            ],
                        ),
                        servers.identify_active(twins_db, plain),
                        servers.identify_active(twins_db, plain, 110),
                        servers.identify_active(twins_db, plain, 999),
                        servers.identify_active(
                            twins_db,
                            [
                                {
                                    "pid": 110,
                                    "connection": None,
                                    "heartbeat_at": "igual",
                                },
                                {
                                    "pid": 111,
                                    "connection": None,
                                    "heartbeat_at": "igual",
                                },
                            ],
                        ),
                    ],
                    [
                        (110, "meta.mcp_pid"),
                        (110, "meta.mcp_pid"),
                        (110, "connection"),
                        (110, "heartbeat_at"),
                        (None, None),
                        (110, "meta del proyecto"),
                        (None, None),
                        (None, None),
                    ],
                    "el activo se identifica por meta, connection o latido, "
                    "en ese orden",
                )
            finally:
                twins_meta.close()

            dry = servers.prune(
                twins_db,
                apply=False,
                process=fake3,
                killer=killer3,
                current="abc",
                live=forged,
                duplicates=True,
                kill=[110],
                grace=0,
                slug=0,
                sleep=lambda _s: None,
            )
            h.ok(
                dry["duplicates"] == [110]
                and dry["killed"] == []
                and dry["deferred"] == [111]
                and 110 in forged
                and 111 in forged,
                "el dry-run con --kill lista el PID exacto sin matarlo",
            )

            forced = servers.prune(
                twins_db,
                apply=True,
                process=fake3,
                killer=killer3,
                current="abc",
                live=forged,
                duplicates=True,
                force=True,
                grace=0,
                slug=0,
                sleep=lambda _s: None,
            )
            h.ok(
                forced["killed"] == [110]
                and 111 in forced["kept"]
                and forced["deferred"] == [111]
                and forced["warnings"]
                and "--force conserva el más nuevo (111)"
                in forced["warnings"][0],
                "con --force se termina sin evidencia y el aviso lo dice",
            )

            forged[110] = {
                "pid": 110,
                "ppid": 50,
                "cmdline": "python3 /x/tickets/server.py",
            }
            killed = servers.prune(
                twins_db,
                apply=True,
                process=fake3,
                killer=killer3,
                current="abc",
                live=forged,
                duplicates=True,
                kill=[111],
                grace=0,
                slug=0,
                sleep=lambda _s: None,
            )
            h.ok(
                killed["killed"] == [111]
                and killed["duplicates"] == [111]
                and killed["deferred"] == [110]
                and 111 not in forged
                and 110 in forged,
                "con --kill se termina exactamente el PID pedido",
            )
            forged[111] = {
                "pid": 111,
                "ppid": 50,
                "cmdline": "python3 /x/tickets/server.py",
            }
            db.meta_set(twins_db, "mcp_pid", "110")
            guarded = servers.prune(
                twins_db,
                apply=True,
                process=fake3,
                killer=killer3,
                current="abc",
                live=forged,
                duplicates=True,
                force=True,
                grace=0,
                slug=0,
                sleep=lambda _s: None,
            )
            h.ok(
                guarded["killed"] == [111]
                and 110 in guarded["kept"]
                and guarded["active"]
                == [{"pid": 110, "evidence": "meta.mcp_pid"}]
                and not guarded["warnings"],
                "el activo identificado se conserva aunque el más nuevo "
                "sea otro",
            )

            forged[111] = {
                "pid": 111,
                "ppid": 50,
                "cmdline": "python3 /x/tickets/server.py",
            }
            project_db = db.connect(":memory:")
            try:
                for pid, beat in ((110, "10:01"), (111, "10:05")):
                    project_db.execute(
                        "INSERT INTO mcp_servers(pid, ppid, name, fingerprint, "
                        "started_at, heartbeat_at) VALUES (?, 50, 'tickets', "
                        "'abc', '2026-10-31T10:00:00-05:00', ?)",
                        (pid, beat),
                    )
                by_project = servers.prune(
                    project_db,
                    apply=True,
                    process=fake3,
                    killer=killer3,
                    current="abc",
                    live=forged,
                    duplicates=True,
                    force=True,
                    active_pid=110,
                    grace=0,
                    slug=0,
                    sleep=lambda _s: None,
                )
                h.ok(
                    by_project["killed"] == [111]
                    and 110 in by_project["kept"]
                    and by_project["active"]
                    == [
                        {"pid": 110, "evidence": "meta del proyecto"}
                    ]
                    and not by_project["warnings"],
                    "el PID activo de la DB del proyecto se conserva aunque "
                    "sea el más viejo y su latido el más antiguo",
                )
            finally:
                project_db.close()
        finally:
            twins_db.close()

        fleet.execute("DELETE FROM mcp_servers")
        for pid, space in ((800, ".config"), (801, "taiki")):
            fleet.execute(
                "INSERT INTO mcp_servers(pid, ppid, name, fingerprint, "
                "started_at, heartbeat_at, workspace) VALUES "
                "(?, 50, 'tickets', 'abc', '2026-10-31T10:00:00-05:00', "
                "'2026-10-31T10:00:00-05:00', ?)",
                (pid, space),
            )
        spaces = {
            item["pid"]: item["state"]
            for item in servers.inventory(
                fleet,
                process=fake_process,
                current="abc",
                live={
                    800: {
                        "pid": 800,
                        "ppid": 50,
                        "cmdline": "python3 /x/taiki/server.py",
                    },
                    801: {
                        "pid": 801,
                        "ppid": 50,
                        "cmdline": "python3 /x/taiki/server.py",
                    },
                },
            )
        }
        h.ok(
            spaces == {800: "al día", 801: "al día"},
            "instancias de proyectos distintos no se marcan duplicadas",
        )

        # ── Recarga por señal: solo registrados, vivos y del workspace ──
        reload_db = db.connect(":memory:")
        try:
            reload_procs = {
                50: {
                    "pid": 50,
                    "ppid": 1,
                    "cmdline": "opencode serve --service",
                },
                100: {
                    "pid": 100,
                    "ppid": 50,
                    "cmdline": "python3 /x/taiki/server.py",
                },
                200: {
                    "pid": 200,
                    "ppid": 1,
                    "cmdline": "python3 /x/taiki/server.py",
                },
                300: {
                    "pid": 300,
                    "ppid": 50,
                    "cmdline": "python3 /x/taiki/server.py",
                },
                600: {
                    "pid": 600,
                    "ppid": 50,
                    "cmdline": "python3 /x/taiki/server.py",
                },
            }

            def reload_process(pid):
                return reload_procs.get(pid)

            for pid, ppid, space in (
                (100, 50, "taiki"),
                (200, 1, "taiki"),
                (300, 50, ".config"),
                (400, 50, "taiki"),
                (500, 50, "taiki"),
            ):
                reload_db.execute(
                    "INSERT INTO mcp_servers(pid, ppid, name, fingerprint, "
                    "started_at, heartbeat_at, workspace) VALUES "
                    "(?, ?, 'taiki', 'abc', '2026-10-31T10:00:00-05:00', "
                    "'2026-10-31T10:00:00-05:00', ?)",
                    (pid, ppid, space),
                )
            # El padre del servicio entra solo como ``process``: en ``live``
            # metería una entrada extra (no es servidor) al inventario.
            live_map = {
                pid: info
                for pid, info in reload_procs.items()
                if pid != 50
            }
            sent: list[tuple[int, int]] = []

            def fake_signaller(pid, sig):
                sent.append((pid, sig))

            selection = servers.reload_processes(
                reload_db,
                workspace="taiki",
                apply=False,
                process=reload_process,
                signaller=fake_signaller,
                current="abc",
                live=live_map,
            )
            sent_dry = list(sent)
            plan = servers.reload_processes(
                reload_db,
                workspace="taiki",
                apply=True,
                process=reload_process,
                signaller=fake_signaller,
                current="abc",
                live=live_map,
            )
            reasons = {item["pid"]: item["reason"] for item in plan["skipped"]}

            def broken_signaller(pid, sig):
                raise OSError("sin permiso")

            undelivered = servers.reload_processes(
                reload_db,
                workspace="taiki",
                apply=True,
                process=reload_process,
                signaller=broken_signaller,
                current="abc",
                live=live_map,
            )
            lazy = servers.reload_processes(
                reload_db,
                apply=False,
                process=lambda pid: None,
                live={},
            )
            h.eq(
                (
                    selection["signalled"],
                    sent_dry,
                    plan["signalled"],
                    list(sent),
                    reasons,
                    (
                        undelivered["signalled"],
                        {
                            item["pid"]: item["reason"]
                            for item in undelivered["skipped"]
                        }[100],
                    ),
                    lazy["reason"],
                ),
                (
                    [100],
                    [],
                    [100],
                    [(100, signal.SIGHUP)],
                    {
                        200: "huérfano",
                        300: "otro workspace",
                        400: "muerto",
                        500: "muerto",
                        600: "sin registro",
                    },
                    ([], "señal no entregada"),
                    "sin servidores vivos de este workspace",
                ),
                "la recarga señala solo registrados vivos del workspace actual",
            )
        finally:
            reload_db.close()

        # ── La espera de la recarga: reloj, huella y sueño inyectados ──
        wait_db = db.connect(":memory:")
        try:
            wait_procs = {
                50: {
                    "pid": 50,
                    "ppid": 1,
                    "cmdline": "opencode serve --service",
                },
                100: {
                    "pid": 100,
                    "ppid": 50,
                    "cmdline": "python3 /x/taiki/server.py",
                },
            }

            def wait_process(pid):
                return wait_procs.get(pid)

            wait_db.execute(
                "INSERT INTO mcp_servers(pid, ppid, name, fingerprint, "
                "started_at, heartbeat_at, workspace) VALUES "
                "(100, 50, 'taiki', 'viejo', '2026-10-31T10:00:00-05:00', "
                "'2026-10-31T10:00:00-05:00', 'taiki')"
            )
            ticks = {"t": 0.0}
            reads = {"n": 0}
            sleeps = {"n": 0}

            def fake_clock():
                return ticks["t"]

            def fake_sleep(_seconds):
                sleeps["n"] += 1
                ticks["t"] += 0.1

            disk = runtime.disk_fingerprint()

            def reader_late(_conn, _pid):
                reads["n"] += 1
                return disk if reads["n"] > 1 else "viejo"

            reloaded = servers.reload_processes(
                wait_db,
                workspace="taiki",
                apply=True,
                process=wait_process,
                signaller=lambda _pid, _sig: None,
                current="viejo",
                live={100: wait_procs[100]},
                wait=True,
                timeout=5.0,
                fingerprint_reader=reader_late,
                clock=fake_clock,
                sleep=fake_sleep,
            )
            h.ok(
                reloaded["statuses"] == {100: "recargado"} and sleeps["n"] == 1,
                "un proceso que registra la huella nueva durante la espera "
                "queda recargado",
            )

            lazy = servers.reload_processes(
                wait_db,
                workspace="taiki",
                apply=True,
                process=wait_process,
                signaller=lambda _pid, _sig: None,
                current="viejo",
                live={100: wait_procs[100]},
                wait=False,
                fingerprint_reader=reader_late,
                clock=fake_clock,
                sleep=fake_sleep,
            )
            h.ok(
                lazy["statuses"] == {}
                and reads["n"] == 2
                and sleeps["n"] == 1,
                "con wait=False no se sondea ni se espera",
            )

            slow = servers.reload_processes(
                wait_db,
                workspace="taiki",
                apply=True,
                process=wait_process,
                signaller=lambda _pid, _sig: None,
                current="viejo",
                live={100: wait_procs[100]},
                wait=True,
                timeout=0.3,
                fingerprint_reader=lambda _conn, _pid: "viejo",
                clock=fake_clock,
                sleep=fake_sleep,
            )
            h.ok(
                slow["statuses"] == {100: "pendiente"} and sleeps["n"] == 4,
                "sin huella nueva al vencer el timeout queda pendiente "
                "(reloj inyectado, sin dormir)",
            )

            silent = servers.reload_processes(
                wait_db,
                workspace="taiki",
                apply=True,
                process=wait_process,
                signaller=lambda _pid, _sig: None,
                current="viejo",
                live={100: wait_procs[100]},
                wait=True,
                fingerprint_reader=lambda _conn, _pid: None,
                clock=fake_clock,
                sleep=fake_sleep,
            )
            h.ok(
                silent["statuses"] == {100: "sin respuesta"}
                and sleeps["n"] == 4,
                "sin huella registrada no hay con qué comparar: sin respuesta",
            )
        finally:
            wait_db.close()

        servers.register(fleet, "taiki", "test")
        row = fleet.execute(
            "SELECT * FROM mcp_servers WHERE pid = ?", (os.getpid(),)
        ).fetchone()
        h.ok(
            row is not None
            and row["fingerprint"] == runtime.code_fingerprint()
            and row["name"] == "taiki",
            "el proceso se registra con su huella y nombre taiki",
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
                domain.StaleServerError,
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
