"""Runtime del MCP: re-exec por señal, sin señales reales.

El manejador de SIGHUP y el re-exec se prueban parcheando ``os.execv`` y
``runtime.is_stale``: la suite nunca envía señales ni reemplaza procesos.
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

        runtime.is_stale = lambda: True
        h.ok(
            not mcp_server.maybe_reexec() and not calls,
            "sin bandera no hay re-exec",
        )

        runtime.is_stale = lambda: False
        mcp_server._reload_requested = True
        with contextlib.redirect_stderr(io.StringIO()) as err:
            ignored = mcp_server.maybe_reexec()
        h.ok(
            not ignored and not calls and "se ignora" in err.getvalue(),
            "una señal sin cambios en el código no re-ejecuta",
        )

        # ── El re-exec ocurre solo con código nuevo y dentro del límite ──
        runtime.is_stale = lambda: True
        mcp_server._reload_requested = True
        with h.env(**{mcp_server.REEXEC_ENV: None}):
            done = mcp_server.maybe_reexec()
            witness = os.environ.get(mcp_server.REEXEC_ENV)
        h.ok(
            done
            and calls == [(sys.executable, [sys.executable, *sys.argv])]
            and witness == "1"
            and not mcp_server._reload_requested,
            "con código viejo re-ejecuta el mismo argv y deja el testigo",
        )

        mcp_server._reload_requested = True
        with h.env(**{mcp_server.REEXEC_ENV: str(mcp_server.REEXEC_MAX)}):
            with contextlib.redirect_stderr(io.StringIO()) as err:
                capped = mcp_server.maybe_reexec()
        h.ok(
            not capped and len(calls) == 1 and "reinicia" in err.getvalue(),
            "agotado el límite de re-exec no insiste",
        )

        os.execv = boom_execv
        mcp_server._reload_requested = True
        with h.env(**{mcp_server.REEXEC_ENV: None}):
            with contextlib.redirect_stderr(io.StringIO()) as err:
                failed = mcp_server.maybe_reexec()
            gone = os.environ.get(mcp_server.REEXEC_ENV)
        os.execv = fake_execv
        h.ok(
            not failed
            and gone is None
            and "no se pudo re-ejecutar" in err.getvalue(),
            "si execv falla, el servidor sigue con el código actual",
        )

        # ── Arranque y relevo: testigo y huella re-registrada ─────────
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
        h.ok(
            closed is None
            and kept == "2"
            and startups == ["signal", "register", "signal", "register"],
            "el arranque re-registra la huella y cierra la cadena al converger",
        )

        # ── Contrato del CLI: flags de recarga ──────────────────────────
        parser_mod = __import__("parser")
        reloaded = parser_mod.build_parser().parse_args(
            ["servers", "--reload", "--dry-run"]
        )
        plain = parser_mod.build_parser().parse_args(["servers"])
        h.ok(
            reloaded.reload and reloaded.dry_run
            and not plain.reload
            and not plain.dry_run,
            "servers expone --reload con --dry-run y no los activa por defecto",
        )
        no_reload = parser_mod.build_parser().parse_args(
            ["mcp", "install", "--no-reload"]
        )
        install_plain = parser_mod.build_parser().parse_args(["mcp", "install"])
        h.ok(
            no_reload.no_reload and not install_plain.no_reload,
            "mcp install expone --no-reload y no lo activa por defecto",
        )
    finally:
        os.execv = original_execv
        runtime.is_stale = original_stale
        mcp_server._reload_requested = original_flag
        mcp_server.signal = original_signal
        mcp_server._register_runtime = original_register
        mcp_server.install_reload_handler = original_install
