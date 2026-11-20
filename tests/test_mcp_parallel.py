"""Herramientas MCP de paralelismo: estado, leases, worktrees y scratch."""

from __future__ import annotations

import subprocess
from datetime import datetime, timedelta
from pathlib import Path

from mcp_layer import handlers
from ticketlib import db, domain, repo, scratch, workspace
from tests.seeds import git_repo
from tests.support import Harness


def _git(root: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(root), *args], capture_output=True, text=True
    )
    if result.returncode != 0:
        raise AssertionError(result.stderr)
    return result.stdout.strip()


def _deniega(func) -> bool:
    """True si la llamada se rechaza con PermissionDenied."""
    try:
        func()
    except domain.PermissionDenied:
        return True
    return False


def run(h: Harness) -> None:
    tmp = Path(h.tmp)
    root = git_repo(tmp / "proyecto")
    (root / "src").mkdir()
    (root / "src" / "app.py").write_text("print('hola')\n", encoding="utf-8")
    _git(root, "add", "-A")
    _git(root, "commit", "-m", "init")
    info = workspace.init_workspace(root, name="Proyecto")
    db_path = str(info["db"])
    # Repositorio donde corre la suite: ningún worktree debe nacer aquí.
    real = workspace.project_root()

    with h.env(TAIKI_DB=db_path):
        conn = db.connect()
        try:
            ticket = repo.create_ticket(conn, title="Paralelo", project="proyecto")
            other = repo.create_ticket(
                conn, title="Con worktree", project="proyecto"
            )
            third = repo.create_ticket(conn, title="Con lease", project="proyecto")
            live_ticket = repo.create_ticket(
                conn, title="Live scope", project="proyecto",
                isolation="scope", paths=["src/app.py"],
            )
            live_other = repo.create_ticket(
                conn, title="Live scope 2", project="proyecto",
                isolation="scope", paths=["src/app.py"],
            )
            scratch_ticket = scratch.create_or_reuse(
                conn, agent="opencode", session_id="ses_scratch"
            )
        finally:
            conn.close()

        meta = {"sessionId": "ses_par"}
        claimed = handlers.call_tool(
            "claim", {"code": ticket["code"], "agent": "opencode"}, meta=meta
        )
        h.eq(claimed["owner_session"], "ses_par", "claim MCP fija la sesión")

        snap = handlers.call_tool("status", {"servers": False}, meta=meta)
        me = handlers.call_tool("whoami", {}, meta=meta)
        h.ok(
            any(
                item["code"] == ticket["code"] and item["session"] == "ses_par"
                for item in snap["sessions"]
            )
            and me["session"] == "ses_par"
            and me["active"]["code"] == ticket["code"],
            "status y whoami devuelven la sesión y su ticket activo",
        )

        reader = db.connect()
        try:
            past = (
                datetime.now().astimezone() - timedelta(minutes=5)
            ).isoformat(timespec="seconds")
            reader.execute(
                "UPDATE tickets SET heartbeat_at = ?, lease_until = ? "
                "WHERE code = ?",
                (past, past, ticket["code"]),
            )
            reader.commit()
        finally:
            reader.close()
        hb = handlers.call_tool("heartbeat", {}, meta=meta)
        h.ok(
            hb["renewed"]
            and hb["lease_until"]
            and _deniega(
                lambda: handlers.call_tool(
                    "heartbeat",
                    {"code": ticket["code"]},
                    meta={"sessionId": "ses_otra"},
                )
            ),
            "heartbeat renueva el lease propio y otra sesión no puede",
        )

        tree = handlers.call_tool(
            "worktree_create", {"code": ticket["code"]}, meta=meta
        )
        h.ok(
            Path(tree["path"]).is_dir()
            and str(tree["branch"]).startswith("agent/")
            and Path(tree["path"])
            == (root / ".worktrees" / ticket["code"]).resolve(),
            "worktree_create crea el worktree y la rama en la raíz de TAIKI_DB",
        )
        listed = handlers.call_tool(
            "worktree_list", {"code": ticket["code"]}, meta=meta
        )
        h.ok(listed["count"] >= 1, "worktree_list encuentra el worktree")
        h.raises(
            domain.PermissionDenied,
            lambda: handlers.call_tool(
                "worktree_create",
                {"code": ticket["code"]},
                meta={"sessionId": "ses_otra"},
            ),
            "otra sesión no crea el worktree de un ticket ajeno",
        )

        tree_claim = handlers.call_tool(
            "claim",
            {"code": other["code"], "agent": "opencode", "worktree": True},
            meta={"sessionId": "ses_tree"},
        )
        h.ok(
            Path(tree_claim["worktree"]).is_dir(),
            "claim con worktree crea y enlaza el árbol",
        )
        leaked_branches = _git(
            real, "branch", "--list", "agent/T-0001*", "agent/T-0002*"
        )
        leaked_trees = [path.name for path in (real / ".worktrees").glob("T-000[12]*")]
        h.ok(
            not leaked_branches and not leaked_trees,
            f"worktree_create no deja ramas ni worktrees en {real}",
        )
        lease_claim = handlers.call_tool(
            "claim",
            {
                "code": third["code"],
                "agent": "opencode",
                "lease_minutes": 5,
            },
            meta={"sessionId": "ses_lease"},
        )
        h.ok(
            lease_claim["lease_until"] is not None,
            "claim acepta lease_minutes",
        )
        live_claim = handlers.call_tool(
            "claim",
            {"code": live_ticket["code"], "agent": "opencode"},
            meta={"sessionId": "ses_live"},
        )
        h.ok(
            live_claim["isolation"] == "scope"
            and live_claim.get("worktree") is None
            and live_claim["requires_worktree"] is False,
            "claim en scope no crea worktree y devuelve el aislamiento",
        )
        h.raises(
            domain.ValidationError,
            lambda: handlers.call_tool(
                "claim",
                {
                    "code": live_other["code"],
                    "agent": "opencode",
                    "worktree": True,
                },
                meta={"sessionId": "ses_live2"},
            ),
            "claim con worktree se rechaza en scope",
        )
        handlers.call_tool(
            "claim",
            {"code": live_other["code"], "agent": "opencode"},
            meta={"sessionId": "ses_live2"},
        )
        h.raises(
            domain.ValidationError,
            lambda: handlers.call_tool(
                "worktree_create",
                {"code": live_other["code"]},
                meta={"sessionId": "ses_live2"},
            ),
            "worktree_create rechaza tickets sin worktree",
        )

        promoted = handlers.call_tool(
            "promote",
            {"code": scratch_ticket["code"]},
            meta={"sessionId": "ses_scratch"},
        )
        h.ok(
            promoted["code"].startswith("T-")
            and not scratch.is_scratch(promoted),
            "promote convierte un scratch en ticket real",
        )

        conn = db.connect()
        try:
            drop_scratch = scratch.create_or_reuse(
                conn, agent="opencode", session_id="ses_drop"
            )
        finally:
            conn.close()
        dropped = handlers.call_tool(
            "scratch_drop",
            {"code": drop_scratch["code"]},
            meta={"sessionId": "ses_drop"},
        )
        h.ok(
            dropped["archived"]
            and dropped["status"] == "cancelado"
            and dropped["owner_session"] is None,
            "scratch_drop archiva y cancela sin dejar claim",
        )

        reassigned = handlers.call_tool(
            "update",
            {"code": other["code"], "assigned_to": "otro-agente"},
            meta={"sessionId": "ses_tree"},
        )
        h.eq(
            reassigned["assigned_to"],
            "otro-agente",
            "update permite reasignar el ticket",
        )

        health = handlers.call_tool("health", {"servers": False}, meta=meta)
        h.ok(
            {"mcp", "hooks", "stale_claims", "overlaps", "ok"} <= set(health)
            and len(health["hooks"]) == 4,
            "health devuelve el estado del MCP y sus cuatro hooks",
        )
