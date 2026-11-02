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

from commands import (  # noqa: F401
    cmd_attach,
    cmd_audit,
    cmd_auto_link,
    cmd_brief,
    cmd_claim,
    cmd_comment,
    cmd_commit,
    cmd_complete,
    cmd_confirm,
    cmd_detach,
    cmd_export,
    cmd_guard,
    cmd_heartbeat,
    cmd_hook_check_index,
    cmd_hook_check_msg,
    cmd_hook_check_push,
    cmd_hook_install,
    cmd_init,
    cmd_link,
    cmd_list,
    cmd_mcp_install,
    cmd_mcp_uninstall,
    cmd_memory,
    cmd_new,
    cmd_next,
    cmd_open,
    cmd_overview,
    cmd_reject,
    cmd_release,
    cmd_remember,
    cmd_renumber,
    cmd_repair_gaps,
    cmd_reopen,
    cmd_scheme_install,
    cmd_scheme_uninstall,
    cmd_scratch,
    cmd_servers,
    cmd_show,
    cmd_stats,
    cmd_status,
    cmd_transition,
    cmd_tui,
    cmd_update,
    cmd_worktree_adopt,
    cmd_worktree_create,
    cmd_worktree_list,
    cmd_worktree_merge,
    cmd_worktree_remove,
)
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
