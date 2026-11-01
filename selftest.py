#!/usr/bin/env python3
"""Compatibilidad: la suite vive en ``tests/``."""

from __future__ import annotations

from tests.run import main

if __name__ == "__main__":
    raise SystemExit(main())
