"""Integración con herdr."""

from __future__ import annotations

import os
from pathlib import Path
import tui
from ticketlib import db, herdr, repo, util
from tests.support import Harness


def run(h: Harness) -> None:
    conn = h.conn
    tmp = str(h.tmp)

    # ── Integración con herdr ────────────────────────────────────
    sock = Path(tmp) / "herdr.sock"
    sock.write_text("")
    fake_bin = Path(tmp) / "herdr"
    fake_bin.write_text("#!/bin/sh\n")
    fake_bin.chmod(0o755)
    saved = {
        key: os.environ.get(key)
        for key in ("HERDR_ENV", "HERDR_SOCKET_PATH", "HERDR_BIN_PATH")
    }
    try:
        os.environ.pop("HERDR_ENV", None)
        os.environ["HERDR_SOCKET_PATH"] = str(sock)
        os.environ["HERDR_BIN_PATH"] = str(fake_bin)
        h.ok(
            herdr.available(),
            "herdr disponible por socket sin HERDR_ENV",
        )
        sock.unlink()
        h.ok(
            not herdr.available(),
            "sin socket ni HERDR_ENV no se usa herdr",
        )
    finally:
        for key, value in saved.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value

    h.ok(
        util.normalize_agent("build") == "opencode"
        and util.normalize_agent("plan") == "opencode"
        and util.normalize_agent("claude") == "claude"
        and util.normalize_agent("") == "",
        "normalize_agent mapea build/plan a opencode",
    )
    normalized = repo.create_ticket(
        conn, title="Agente normalizado", created_by="build"
    )
    repo.claim_ticket(conn, normalized["code"], agent="plan")
    refreshed = repo.get_ticket(conn, normalized["code"])
    h.ok(
        refreshed["created_by"] == "opencode"
        and refreshed["assigned_to"] == "opencode",
        "crear y reclamar normalizan el agente",
    )
    conn.execute(
        "UPDATE tickets SET created_by = 'build' WHERE id = ?",
        (refreshed["id"],),
    )
    db.migrate(conn, force=True)
    h.ok(
        repo.get_ticket(conn, normalized["code"])["created_by"]
        == "opencode",
        "la migración corrige agentes antiguos",
    )
    h.ok(
        herdr.agent_name("T-0029.01") == "t-0029-01"
        and herdr.kind_for("antigravity") == "agy"
        and herdr.resume_args("opencode", "ses_x") == ["--session", "ses_x"],
        "herdr: nombre de agente, kind y reanudación",
    )
    home = str(Path.home())
    config_dir = str(Path.home() / ".config")
    panes_h = [
        {
            "pane_id": "w1:p1",
            "tab_id": "w1:t1",
            "workspace_id": "w1",
            "cwd": home,
        },
        {
            "pane_id": "w2:p1",
            "tab_id": "w2:t1",
            "workspace_id": "w2",
            "cwd": config_dir,
        },
        {
            "pane_id": "w2:p2",
            "tab_id": "w2:t2",
            "workspace_id": "w2",
            "cwd": config_dir,
            "agent": "opencode",
        },
    ]
    h.ok(
        herdr.deepest_workspace(panes_h, f"{config_dir}/tickets") == "w2"
        and herdr.deepest_workspace(panes_h, "/tmp") is None,
        "herdr elige el workspace del directorio más cercano",
    )
    h.ok(
        tui.muted_style("confirmado") == (True, False)
        and tui.muted_style("cancelado") == (True, True)
        and tui.muted_style("en_progreso") == (False, False),
        "confirmados y cancelados se atenúan; cancelado pide tachado",
    )
    h.ok(
        herdr.tab_label("T-0032", "título largo que no debe ir") == "T-0032"
        and herdr.find_tab(
            [{"tab_id": "w2:t9", "label": "T-0032"}], "T-0032"
        )["tab_id"]
        == "w2:t9"
        and herdr.free_pane(panes_h, "w1:t1")["pane_id"] == "w1:p1"
        and herdr.free_pane(panes_h, "w2:t2") is None,
        "herdr reutiliza la pestaña del ticket y detecta el pane libre",
    )

    herdr_calls = []
    live_agents = [
        {"pane_id": "w1:pQ", "agent_session": {"value": "ses_live"}}
    ]

    def fake_herdr(argv):
        herdr_calls.append(list(argv))
        if argv[:2] == ["agent", "list"]:
            return {"result": {"agents": live_agents}}
        if argv[:2] == ["pane", "list"]:
            return {"result": {"panes": panes_h}}
        if argv[:2] == ["workspace", "list"]:
            return {"result": {"workspaces": []}}
        if argv[:2] == ["tab", "list"]:
            return {"result": {"tabs": []}}
        if argv[:2] == ["workspace", "create"]:
            return {
                "result": {
                    "workspace": {"workspace_id": "w9"},
                    "tab": {"tab_id": "w9:t1"},
                    "root_pane": {"pane_id": "w9:p1"},
                }
            }
        if argv[:2] == ["tab", "create"]:
            return {
                "result": {
                    "tab": {"tab_id": "w2:t7"},
                    "root_pane": {"pane_id": "w2:p7"},
                }
            }
        return {"result": {}}

    focus_plan = herdr.plan(
        code="T-0032",
        title="Abrir en herdr",
        path=f"{config_dir}/tickets",
        session_id="ses_live",
        backend="opencode",
        run=fake_herdr,
    )
    h.ok(
        focus_plan["action"] == "focus"
        and focus_plan["pane_id"] == "w1:pQ",
        "herdr enfoca la sesión viva en vez de duplicarla",
    )
    fresh = herdr.plan(
        code="T-0032",
        title="Abrir en herdr",
        path="/nuevo/proyecto",
        session_id=None,
        backend="opencode",
        run=fake_herdr,
    )
    h.ok(
        fresh["action"] == "open"
        and fresh["steps"][0][:2] == ["workspace", "create"]
        and any(step[:2] == ["tab", "create"] for step in fresh["steps"]),
        "herdr planifica workspace y pestaña si no existen",
    )
    opened = herdr.open_ticket(
        code="T-0032",
        title="Abrir en herdr",
        path="/nuevo/proyecto",
        session_id=None,
        backend="opencode",
        run=fake_herdr,
    )
    h.ok(
        opened["action"] == "opened"
        and opened["workspace_id"] == "w9"
        and opened["pane_id"] == "w9:p1",
        "herdr ejecuta workspace, pestaña y agente",
    )

    link_ticket = repo.create_ticket(conn, title="Abrir en herdr")
    seen = {"started": False}

    def linking_herdr(argv):
        if argv[:2] == ["agent", "list"]:
            if seen["started"]:
                return {
                    "result": {
                        "agents": [
                            {
                                "pane_id": "w9:p1",
                                "agent_session": {"value": "ses_nueva"},
                            }
                        ]
                    }
                }
            return {"result": {"agents": []}}
        if argv[:2] in (["pane", "list"], ["workspace", "list"], ["tab", "list"]):
            return {"result": {"panes": [], "workspaces": [], "tabs": []}}
        if argv[:2] == ["workspace", "create"]:
            return {
                "result": {
                    "workspace": {"workspace_id": "w9"},
                    "tab": {"tab_id": "w9:t1"},
                    "root_pane": {"pane_id": "w9:p1"},
                }
            }
        if argv[:2] == ["agent", "start"]:
            seen["started"] = True
        return {"result": {}}

    linked = herdr.open_and_link(
        conn,
        link_ticket,
        None,
        backend="opencode",
        directory="/nuevo/proyecto",
        run=linking_herdr,
        wait=0.2,
    )
    fresh_ticket = repo.get_ticket(conn, link_ticket["code"])
    h.ok(
        linked["session_id"] == "ses_nueva"
        and any(
            item["ref"] == "ses_nueva" for item in fresh_ticket["links"]
        ),
        "herdr enlaza la sesión nueva al ticket",
    )
