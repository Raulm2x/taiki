"""Ejecución de herramientas, recursos y prompts MCP.

Este módulo es la fachada pública del dominio MCP. Los manejadores viven en
módulos hermanos por dominio (tickets, orquestación y recursos); aquí quedan el
despacho, ``call_tool`` y el health, que comparte utilidades con la fachada.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from ticketlib import (
    audit as audit_lib,
    db,
    domain,
    githook,
    repo,
    runtime,
    servers as servers_lib,
    workspace,
)
from ticketlib.memory.config import load_memory_config
from ticketlib.memory.models import PLAYBOOK_KIND
from ticketlib.memory.playbooks import SKILL_FILE, SKILLS_RELATIVE, slug_of
from ticketlib.memory.stats import stats as memory_stats
from ticketlib.memory.store import list_memories
from ticketlib.util import now, parse_datetime

from . import memory as memory_handlers
from . import schemas, skills as skills_handlers, validation
from . import handler_orchestration as _handler_orchestration
from . import handler_tickets as _handler_tickets
from .handler_common import (
    DETAIL_SECTIONS,
    IDEMPOTENCY_PREFIX,
    PREVIEW_CHARS,
    RECEIPT_FIELDS,
    ToolHandler,
    _actor,
    _project_root,
    _project_rows,
    _receipt,
    _session,
    _summary,
    resolve_session,
    set_client_info,
)
from .handler_orchestration import (
    _handle_agents,
    _handle_checkpoint,
    _handle_heartbeat,
    _handle_promote,
    _handle_reassign,
    _handle_retry,
    _handle_schedule,
    _handle_scratch_drop,
    _handle_status,
    _handle_whoami,
    _handle_worktree_create,
    _handle_worktree_list,
)
from .handler_resources import get_prompt, read_resource
from .handler_tickets import (
    _attach_worktree,
    _handle_claim,
    _handle_comment,
    _handle_complete,
    _handle_create,
    _handle_get,
    _handle_link,
    _handle_list,
    _handle_next,
    _handle_stats,
    _handle_transition,
    _handle_update,
)

# Los límites de secciones se aplican en SQL dentro de get_ticket.

# ---------------------------------------------------------------------------
# Health de memoria
# ---------------------------------------------------------------------------


def _memory_health(conn: sqlite3.Connection, root: Path) -> dict[str, Any]:
    """Integridad, cola y artefactos de la memoria; sin bloquear por avisos."""
    cfg = load_memory_config()
    issues: list[str] = []
    db_ok = True
    required = (
        "memories",
        "memory_evidence",
        "memory_files",
        "memory_usage",
        "memory_ledger",
        "memory_blobs",
        "memories_fts",
    )
    missing: list[str] = []
    for table in required:
        try:
            conn.execute(f"SELECT 1 FROM {table} LIMIT 1").fetchone()
        except sqlite3.DatabaseError:
            missing.append(table)
    if missing:
        db_ok = False
        issues.append(
            "error: faltan tablas de memoria o no son legibles: "
            + ", ".join(missing)
        )

    entries = pending = stale = 0
    budget: dict[str, Any] = {}
    if db_ok:
        try:
            data = memory_stats(conn, cfg)
        except sqlite3.DatabaseError as exc:
            db_ok = False
            issues.append(f"error: no se pudieron leer las métricas de memoria: {exc}")
        else:
            entries = int(data["total"])
            pending = int(data["by_status"]["proposed"])
            stale = int(data["by_status"]["stale"])
            budget = data["budget"]
            try:
                fts_entries = int(
                    conn.execute("SELECT COUNT(*) FROM memories_fts").fetchone()[0]
                )
            except sqlite3.DatabaseError as exc:
                db_ok = False
                issues.append(f"error: FTS de memoria ilegible: {exc}")
            else:
                if fts_entries != entries:
                    db_ok = False
                    issues.append(
                        "error: FTS de memoria desincronizado: "
                        f"{fts_entries} indizadas frente a {entries} filas"
                    )

    if pending:
        base = parse_datetime(now())
        old: list[str] = []
        if base is not None:
            for memory in list_memories(conn, status="proposed", limit=1000):
                created = parse_datetime(
                    memory.get("created_at") or memory.get("updated_at")
                )
                if created is not None and created <= base - timedelta(days=7):
                    old.append(str(memory.get("code") or ""))
        if old:
            issues.append(
                f"warning: {len(old)} propuesta(s) de memoria llevan más de 7 "
                f"días sin aprobar: {', '.join(old[:5])}"
            )
    if stale:
        issues.append(
            f"warning: {stale} memoria(s) en stale; ejecuta taiki memory curate"
        )
    for scope in ("project", "user"):
        item = budget.get(scope) or {}
        free = int(item.get("chars_to_free") or 0)
        if free > 0:
            issues.append(
                f"warning: presupuesto de memoria {scope} excedido en "
                f"{free} caracteres"
            )

    brief = Path(root) / ".taiki" / "generated" / "brief.md"
    missing_skills: list[str] = []
    if cfg["skills_sync"]:
        for memory in list_memories(
            conn, kind=PLAYBOOK_KIND, status="active", limit=1000
        ):
            slug = slug_of(memory)
            if slug is None:
                continue
            target = Path(root) / SKILLS_RELATIVE / slug / SKILL_FILE
            if not target.is_file():
                missing_skills.append(slug)
    generated_synced = brief.is_file() and not missing_skills
    if not brief.is_file():
        issues.append("warning: falta .taiki/generated/brief.md; usa taiki brief --write")
    if missing_skills:
        issues.append(
            "warning: faltan skills generados: " + ", ".join(sorted(missing_skills))
        )

    return {
        "enabled": bool(cfg["enabled"]),
        "db_ok": db_ok,
        "entries": entries,
        "pending": pending,
        "stale": stale,
        "budget": budget,
        "generated_synced": generated_synced,
        "issues": issues,
    }


def _handle_health(
    conn: sqlite3.Connection,
    args: dict[str, Any],
    session: str | None = None,
    actor: str = "agente",
) -> dict[str, Any]:
    root = _project_root()
    try:
        days = max(1, min(int(args.get("days", 2)), 365))
    except (TypeError, ValueError):
        days = 2
    stale = [item["code"] for item in repo.stale_claims(conn)]
    overlaps = audit_lib.overlaps(conn)
    unresolved = audit_lib.unresolved(conn)
    dirty_files = audit_lib.dirty(root)
    proposed = sorted(
        list_memories(conn, status="proposed", limit=1000),
        key=lambda item: str(item.get("code") or ""),
    )
    cutoff = datetime.now().astimezone() - timedelta(days=7)
    old: list[str] = []
    for memory in proposed:
        created = parse_datetime(
            memory.get("created_at") or memory.get("updated_at")
        )
        if created is not None and created <= cutoff:
            old.append(str(memory.get("code") or ""))
    memory_warnings: list[str] = []
    if old:
        memory_warnings.append(
            f"{len(old)} propuestas de memoria llevan más de 7 días sin "
            f"aprobar: {', '.join(old[:5])}; revisa con taiki memory pending"
        )
    memory = _memory_health(conn, root)
    data: dict[str, Any] = {
        "ok": (
            not stale
            and not overlaps
            and not unresolved
            and not dirty_files
            and memory["db_ok"]
        ),
        "mcp": runtime.status(conn),
        "hooks": githook.hooks_installed(root),
        "stale_claims": stale,
        "overlaps": overlaps[:20],
        "unresolved": unresolved[:20],
        "dirty": dirty_files[:20],
        "gaps": audit_lib.code_gaps(conn)[:20],
        "stalled": audit_lib.stalled(conn, days=days)[:20],
        "worktrees": audit_lib.worktrees(conn, root)[:20],
        "memory_pending": {
            "count": len(proposed),
            "proposed_codes": [
                str(memory.get("code") or "") for memory in proposed
            ],
            "stale_days": 7,
            "old": old,
            "warnings": memory_warnings,
        },
        "memory": memory,
    }
    if memory_warnings:
        data["warnings"] = memory_warnings
    if args.get("servers", True):
        registry = db.connect(workspace.registry_path())
        try:
            data["servers"] = servers_lib.summary(
                servers_lib.inventory(registry)
            )
        finally:
            registry.close()
    return data


# ---------------------------------------------------------------------------
# Tabla de Despacho (Registry)
# ---------------------------------------------------------------------------

_DOMAIN_MODULES = (_handler_tickets, _handler_orchestration)


def _lookup_handler(name: str) -> ToolHandler:
    """Busca ``_handle_<name>`` en la fachada y en los módulos de dominio."""
    local = globals().get(f"_handle_{name}")
    if local is not None:
        return local
    for module in _DOMAIN_MODULES:
        found = getattr(module, f"_handle_{name}", None)
        if found is not None:
            return found
    raise KeyError(name)


TOOL_DISPATCH: dict[str, ToolHandler] = {
    spec.name: _lookup_handler(spec.name)
    for spec in schemas.TOOL_SPECS
    if spec.name not in memory_handlers.HANDLERS
    and spec.name not in skills_handlers.HANDLERS
}
TOOL_DISPATCH.update(memory_handlers.HANDLERS)
TOOL_DISPATCH.update(skills_handlers.HANDLERS)
MUTATING_TOOLS = frozenset(
    spec.name for spec in schemas.TOOL_SPECS if not spec.read_only
)


# ---------------------------------------------------------------------------
# Punto de Entrada Principal para Despacho de Herramientas
# ---------------------------------------------------------------------------


def call_tool(
    name: str, args: dict[str, Any] | None = None, meta: Any = None
) -> dict[str, Any]:
    """Despacha la ejecución de una herramienta MCP utilizando TOOL_DISPATCH."""
    args = args or {}
    validation.validate_arguments(name, args)
    schema = validation.tool_schema(name)
    base = schema["name"] if schema else name
    if base in MUTATING_TOOLS and runtime.is_stale():
        raise domain.StaleServerError(
            "El MCP está desactualizado: no se ejecuta la escritura para no "
            "dejar datos inconsistentes. Recarga el servidor MCP (el plugin lo "
            "hace al quedar la sesión idle) o reinicia OpenCode."
        )

    handler = TOOL_DISPATCH.get(name)
    if not handler:
        for prefix in ("taiki_", "tickets_"):
            if name.startswith(prefix):
                handler = TOOL_DISPATCH.get(name.removeprefix(prefix))
                if handler:
                    break

    if not handler:
        raise domain.NotFoundError(f"Herramienta desconocida: {name}")

    conn = db.connect()
    try:
        session = resolve_session(meta=meta, args=args)
        actor = _actor(args)
        return handler(conn, args, session, actor)
    finally:
        conn.close()


__all__ = [
    "TOOL_DISPATCH",
    "call_tool",
    "get_prompt",
    "read_resource",
    "resolve_session",
    "set_client_info",
]
