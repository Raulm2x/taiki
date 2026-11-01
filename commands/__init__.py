"""Comandos del CLI de tickets, agrupados por responsabilidad."""

from __future__ import annotations

from .tickets import (
    cmd_list,
    cmd_scratch,
    cmd_show,
    cmd_new,
    cmd_update,
    cmd_attach,
    cmd_claim,
    cmd_next,
    cmd_repair_gaps,
    cmd_renumber,
    cmd_stats,
    cmd_overview,
    cmd_detach,
    cmd_export,
    cmd_init,
)
from .workflow import (
    cmd_transition,
    cmd_confirm,
    cmd_reopen,
    cmd_complete,
    cmd_comment,
    cmd_reject,
)
from .git import (
    cmd_audit,
    cmd_link,
    cmd_auto_link,
    cmd_hook_check_msg,
    cmd_hook_check_push,
    cmd_hook_install,
)
from .guard import cmd_guard
from .mcp import cmd_mcp_install, cmd_mcp_uninstall
from .servers import cmd_servers
from .system import (
    cmd_open,
    cmd_scheme_install,
    cmd_scheme_uninstall,
    cmd_tui,
)

__all__ = [
    "cmd_list",
    "cmd_show",
    "cmd_new",
    "cmd_update",
    "cmd_attach",
    "cmd_claim",
    "cmd_next",
    "cmd_repair_gaps",
    "cmd_renumber",
    "cmd_stats",
    "cmd_scratch",
    "cmd_overview",
    "cmd_detach",
    "cmd_export",
    "cmd_init",
    "cmd_transition",
    "cmd_confirm",
    "cmd_reopen",
    "cmd_complete",
    "cmd_comment",
    "cmd_reject",
    "cmd_link",
    "cmd_auto_link",
    "cmd_hook_check_msg",
    "cmd_hook_check_push",
    "cmd_hook_install",
    "cmd_audit",
    "cmd_guard",
    "cmd_mcp_install",
    "cmd_mcp_uninstall",
    "cmd_servers",
    "cmd_open",
    "cmd_scheme_install",
    "cmd_scheme_uninstall",
    "cmd_tui",
]
