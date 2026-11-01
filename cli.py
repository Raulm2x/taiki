#!/usr/bin/env python3
"""Punto de entrada del CLI de tickets.

La implementación está dividida en:

- ``commands.py``  — un comando por subcomando del CLI.
- ``parser.py``    — argumentos y ``main()``.
- ``ticketlib/``   — dominio, almacén, sesiones y presentación.

Este módulo se mantiene como fachada para ``bin/taiki`` y para las pruebas.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from commands import *  # noqa: F401,F403
from parser import build_parser, main  # noqa: F401
from ticketlib.githook import (  # noqa: F401
    HOOK_BEGIN,
    HOOK_BLOCK,
    HOOK_END,
    git,
    install_commit_msg_hook,
    install_post_commit_hook,
    install_pre_commit_hook,
    install_pre_push_hook,
)

if __name__ == "__main__":
    raise SystemExit(main())
