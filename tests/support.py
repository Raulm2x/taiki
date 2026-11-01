"""Infraestructura común de la suite: aislamiento, entorno y aserciones."""

from __future__ import annotations

import os
import tempfile
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

from ticketlib import db

# Variables que cada módulo debe poder fijar sin filtrarse al siguiente.
ISOLATED_ENV = (
    "TAIKI_DB",
    "TAIKI_GUARD",
    "TAIKI_GUARD_AUTO",
    "TAIKI_HOOK",
    "TAIKI_AGENT",
    "TICKETS_DB",
    "TICKETS_GUARD",
    "TICKETS_GUARD_AUTO",
    "TICKETS_HOOK",
    "TICKETS_AGENT",
    "OPENCODE_DB",
    "OPENCODE_SESSION_ID",
    "XDG_DATA_HOME",
    "XDG_CONFIG_HOME",
    "ANTIGRAVITY_APP_DATA_DIR",
    "ANTIGRAVITY_CONVERSATION_ID",
    "AGY_SESSION_ID",
    "SESSION_ID",
    "HERDR_ENV",
    "HERDR_SOCKET_PATH",
    "HERDR_BIN_PATH",
)


class Harness:
    """Contexto de un módulo: DB temporal propia, entorno limpio y contador."""

    def __init__(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix="tickets-suite-")
        self.tmp = Path(self._tmp.name)
        self._saved: dict[str, str | None] = {}
        self._stack: list[dict[str, str | None]] = []
        self.conn: Any = None
        self.checks = 0
        self._clean_env()

    def _clean_env(self) -> None:
        for name in ISOLATED_ENV:
            self._saved[name] = os.environ.pop(name, None)

    def _restore(self, saved: dict[str, str | None]) -> None:
        for name, value in saved.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value

    def __enter__(self) -> "Harness":
        self._apply_env(TAIKI_DB=str(self.tmp / "tickets.db"))
        expected = (self.tmp / "tickets.db").resolve()
        if db.db_path().resolve() != expected:
            raise RuntimeError(
                f"La suite abriría la DB real ({db.db_path()}); se aborta para no contaminarla"
            )
        self.conn = db.connect()
        return self

    def _apply_env(self, **values: str | None) -> None:
        saved = {name: os.environ.get(name) for name in values}
        for name, value in values.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = str(value)
        self._stack.append(saved)

    def __exit__(self, exc_type, exc, tb) -> None:
        if self.conn is not None:
            self.conn.close()
        while self._stack:
            self._restore(self._stack.pop())
        self._restore(self._saved)
        self._tmp.cleanup()

    @contextmanager
    def env(self, **values: str | None) -> Iterator[None]:
        """Fija variables de entorno y las restaura al salir."""
        self._apply_env(**values)
        try:
            yield
        finally:
            self._restore(self._stack.pop())

    def ok(self, condition: bool, message: str) -> None:
        if not condition:
            raise AssertionError(message)
        self.checks += 1

    def eq(self, actual: Any, expected: Any, message: str) -> None:
        self.ok(actual == expected, f"{message} (esperado {expected!r}, obtenido {actual!r})")

    def raises(self, exc_type, func, message: str) -> None:
        try:
            func()
        except exc_type:
            self.checks += 1
        else:
            raise AssertionError(message)
