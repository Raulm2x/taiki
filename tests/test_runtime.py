"""Runtime del MCP: re-exec por señal, sin señales reales.

El manejador de SIGHUP y el re-exec se prueban parcheando ``os.execv`` y
``runtime.is_stale``: la suite nunca envía señales ni reemplaza procesos.
Cada fase del ciclo captura una tupla y la comparación es única por grupo
(así el ticket paga su propio crecimiento en el techo de checks).
"""

from __future__ import annotations

import contextlib
import io
import os
import signal as signal_mod
import sys
import types

import server as mcp_server
from ticketlib import runtime
from tests.support import Harness


def run(h: Harness) -> None:
    original_execv = os.execv
    original_stale = runtime.is_stale
    original_flag = mcp_server._reload_requested
    original_signal = mcp_server.signal
    original_register = mcp_server._register_runtime
    original_install = mcp_server.install_reload_handler
    calls: list[tuple[str, list[str]]] = []

    def fake_execv(path: str, argv: list[str]) -> None:
        calls.append((path, argv))

    def boom_execv(path: str, argv: list[str]) -> None:
        raise OSError("sin permiso de exec")

    os.execv = fake_execv
    mcp_server._reload_requested = False
    try:
        # ── El manejador marca una bandera; nunca trabaja en la señal ──
        previous = signal_mod.getsignal(signal_mod.SIGHUP)
        try:
            installed = mcp_server.install_reload_handler()
            handler = signal_mod.getsignal(signal_mod.SIGHUP)
        finally:
            signal_mod.signal(signal_mod.SIGHUP, previous)
        mcp_server.signal = types.SimpleNamespace()  # plataforma sin SIGHUP
        bare = mcp_server.install_reload_handler()
        mcp_server.signal = original_signal
        mcp_server._request_reload(signal_mod.SIGHUP, None)
        flagged = mcp_server._reload_requested
        mcp_server._reload_requested = False
        h.ok(
            installed
            and handler is mcp_server._request_reload
            and not bare
            and flagged,
            "SIGHUP solo instala un marcador de bandera y sin señal no rompe",
        )

        # ── Ciclo de recarga completo, una estructura por grupo ─────────
        runtime.is_stale = lambda: False
        no_flag = (mcp_server.maybe_reexec(), len(calls))
        mcp_server._reload_requested = True
        with contextlib.redirect_stderr(io.StringIO()) as err_fresh:
            fresh = (mcp_server.maybe_reexec(), "se ignora" in err_fresh.getvalue())
        runtime.is_stale = lambda: True
        mcp_server._reload_requested = True
        with h.env(**{mcp_server.REEXEC_ENV: None}):
            reloaded = mcp_server.maybe_reexec()
            witness = os.environ.get(mcp_server.REEXEC_ENV)
        mcp_server._reload_requested = True
        with h.env(**{mcp_server.REEXEC_ENV: str(mcp_server.REEXEC_MAX)}):
            with contextlib.redirect_stderr(io.StringIO()) as err_max:
                capped = mcp_server.maybe_reexec()
        os.execv = boom_execv
        mcp_server._reload_requested = True
        with h.env(**{mcp_server.REEXEC_ENV: None}):
            with contextlib.redirect_stderr(io.StringIO()) as err_boom:
                failed = mcp_server.maybe_reexec()
            gone = os.environ.get(mcp_server.REEXEC_ENV)
        os.execv = fake_execv
        startups: list[str] = []

        def fake_install() -> bool:
            startups.append("signal")
            return True

        mcp_server.install_reload_handler = fake_install
        mcp_server._register_runtime = lambda: startups.append("register")
        runtime.is_stale = lambda: False
        with h.env(**{mcp_server.REEXEC_ENV: "2"}):
            mcp_server._startup()
            closed = os.environ.get(mcp_server.REEXEC_ENV)
        runtime.is_stale = lambda: True
        with h.env(**{mcp_server.REEXEC_ENV: "2"}):
            mcp_server._startup()
            kept = os.environ.get(mcp_server.REEXEC_ENV)
        h.eq(
            (
                no_flag,
                fresh,
                (reloaded, calls, witness, mcp_server._reload_requested),
                (capped, "reinicia" in err_max.getvalue()),
                (failed, gone, "no se pudo" in err_boom.getvalue()),
                (closed, kept, list(startups)),
            ),
            (
                (False, 0),
                (False, True),
                (
                    True,
                    [(sys.executable, [sys.executable, *sys.argv])],
                    "1",
                    False,
                ),
                (False, True),
                (False, None, True),
                (None, "2", ["signal", "register", "signal", "register"]),
            ),
            "la recarga re-ejecuta solo con código nuevo y converge "
            "sin romper el bucle",
        )

        # ── Contrato del CLI: flags de recarga ──────────────────────────
        parser_mod = __import__("parser")
        reload_args = parser_mod.build_parser().parse_args(
            ["servers", "--reload", "--dry-run"]
        )
        plain = parser_mod.build_parser().parse_args(["servers"])
        no_reload = parser_mod.build_parser().parse_args(
            ["mcp", "install", "--no-reload"]
        )
        install_plain = parser_mod.build_parser().parse_args(["mcp", "install"])
        h.eq(
            (
                (reload_args.reload, reload_args.dry_run),
                (plain.reload, plain.dry_run),
                (no_reload.no_reload, install_plain.no_reload),
            ),
            ((True, True), (False, False), (True, False)),
            "servers --reload/--dry-run y mcp install --no-reload existen "
            "y no se activan por defecto",
        )
    finally:
        os.execv = original_execv
        runtime.is_stale = original_stale
        mcp_server._reload_requested = original_flag
        mcp_server.signal = original_signal
        mcp_server._register_runtime = original_register
        mcp_server.install_reload_handler = original_install
