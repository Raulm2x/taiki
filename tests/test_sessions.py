"""Sesiones de agente, enlaces y terminales."""

from __future__ import annotations

import json
import os
import sqlite3
from pathlib import Path
from mcp_layer import handlers
from ticketlib import deeplink, domain, render, repo, sessions
from tests.support import Harness


def run(h: Harness) -> None:
    conn = h.conn
    tmp = str(h.tmp)

    # ── Sesiones de agente, enlaces ticket:// y TUI ─────────────────────
    oc_db = Path(tmp) / "opencode.db"
    oc = sqlite3.connect(oc_db)
    oc.execute(
        "CREATE TABLE session_v2 (id TEXT PRIMARY KEY, directory TEXT, title TEXT)"
    )
    oc.execute(
        "INSERT INTO session_v2 VALUES ('ses_viva1', ?, 'viva')",
        (str(Path.home() / ".config"),),
    )
    oc.execute("INSERT INTO session_v2 VALUES ('ses_viva2', '/tmp', 'otra')")
    oc.commit()
    oc.close()

    previous_open = os.environ.get("OPENCODE_DB")
    previous_xdg_data = os.environ.get("XDG_DATA_HOME")
    previous_xdg_config = os.environ.get("XDG_CONFIG_HOME")
    previous_agy_data = os.environ.get("ANTIGRAVITY_APP_DATA_DIR")
    os.environ["OPENCODE_DB"] = str(oc_db)
    os.environ["XDG_DATA_HOME"] = str(Path(tmp) / "xdg-data")
    os.environ["XDG_CONFIG_HOME"] = str(Path(tmp) / "xdg-config")
    agy_app_dir = Path(tmp) / "agy-app"
    os.environ["ANTIGRAVITY_APP_DATA_DIR"] = str(agy_app_dir)

    agy_session_dir = agy_app_dir / "brain" / "agy_session_123"
    (agy_session_dir / ".system_generated" / "logs").mkdir(parents=True, exist_ok=True)
    transcript_file = agy_session_dir / ".system_generated" / "logs" / "transcript.jsonl"
    transcript_file.write_text(
        json.dumps({"content": "<USER_REQUEST>\nPrueba Antigravity T-0005\n</USER_REQUEST>"}) + "\n",
        encoding="utf-8",
    )

    h.ok(sessions.opencode_db_path() == oc_db, "OPENCODE_DB sobrescribe la ruta")
    h.ok(
        sessions.existing_sessions(["ses_viva1", "agy_session_123", "ses_fantasma"])
        == {"ses_viva1", "agy_session_123"},
        "existing_sessions descarta las sesiones borradas e incluye Antigravity",
    )
    agy_info = sessions.session_info(["agy_session_123"])["agy_session_123"]
    h.ok(
        agy_info["backend"] == "antigravity"
        and agy_info["title"] == "Prueba Antigravity T-0005",
        "session_info resuelve metadatos de sesión de Antigravity",
    )
    h.ok(
        sessions.session_info(["ses_viva1"])["ses_viva1"]["directory"]
        == str(Path.home() / ".config"),
        "session_info devuelve el directorio de la sesión",
    )
    h.ok(sessions.existing_sessions([]) == set(), "sin ids no se consulta nada")

    t_live = repo.create_ticket(conn, title="Sesión viva")
    repo.claim_ticket(conn, t_live["code"], agent="opencode")
    repo.comment_ticket(
        conn, t_live["code"], "nota", session_id="ses_viva1"
    )
    t_dead = repo.create_ticket(conn, title="Sesión perdida")
    repo.claim_ticket(conn, t_dead["code"], agent="antigravity")
    repo.comment_ticket(
        conn, t_dead["code"], "vieja", session_id="ses_fantasma"
    )
    repo.comment_ticket(conn, t_dead["code"], "buena", session_id="ses_viva2")
    repo.comment_ticket(
        conn, t_dead["code"], "nueva", session_id="ses_borrada"
    )

    resolved = sessions.ticket_sessions(
        conn, [t_live["code"], t_dead["code"]]
    )
    h.ok(
        resolved[t_live["code"]] == "ses_viva1",
        "ticket_sessions encuentra la sesión viva",
    )
    h.ok(
        resolved[t_dead["code"]] == "ses_viva2",
        "ticket_sessions retrocede a la sesión viva anterior",
    )

    t_link = repo.create_ticket(conn, title="Solo enlace de sesión")
    repo.link_ticket(conn, t_link["code"], "sesion", "ses_viva1")
    h.ok(
        sessions.ticket_sessions(conn, [t_link["code"]])[t_link["code"]]
        == "ses_viva1",
        "el enlace 'sesion' sirve de respaldo",
    )

    # Agente automático: sin claim, el actor del último evento manda
    t_unclaimed = repo.create_ticket(
        conn,
        title="Creado sin reclamar",
        assigned_to="opencode",
        session_id="ses_viva1",
    )
    h.ok(
        t_unclaimed["assigned_to"] == "opencode",
        "create_ticket guarda el agente que lo crea",
    )
    h.ok(
        sessions.ticket_sessions(conn, [t_unclaimed["code"]])[
            t_unclaimed["code"]
        ]
        == "ses_viva1"
        and repo.get_ticket(conn, t_unclaimed["code"])["assigned_to"]
        == "opencode",
        "un ticket recién creado ya tiene sesión y agente",
    )

    t_bare = repo.create_ticket(conn, title="Sin agente")
    repo.comment_ticket(
        conn, t_bare["code"], "nota", author="codex", session_id="ses_viva2"
    )
    activity = sessions.ticket_activity(
        conn, [t_bare["code"], t_live["code"]]
    )
    h.ok(
        activity[t_bare["code"]]["actor"] == "codex",
        "ticket_activity devuelve el último actor",
    )
    h.ok(
        activity[t_bare["code"]]["session"] == "ses_viva2",
        "ticket_activity devuelve la sesión viva",
    )
    repo.link_ticket(
        conn,
        t_bare["code"],
        "commit",
        "abc9999",
        actor="agente",
    )
    latest = sessions.ticket_activity(conn, [t_bare["code"]])
    h.ok(
        latest[t_bare["code"]]["actor"] == "codex",
        "un evento genérico del hook no tapa al actor real",
    )
    sessions.decorate([t_bare], activity)
    h.ok(
        t_bare["last_actor"] == "codex",
        "decorate rellena last_actor cuando no hay assigned_to",
    )
    h.ok(
        "last_actor" not in t_live or t_live.get("assigned_to"),
        "decorate respeta el assigned_to existente",
    )
    fallback_table = render.render_table(
        [t_bare],
        use_color=True,
        session_map={t_bare["code"]: "ses_viva2"},
    )
    h.ok(
        "codex" in fallback_table
        and f"ticket://open/{t_bare['code']}" in fallback_table,
        "la tabla enlaza al agente de respaldo",
    )
    h.ok(
        sessions.agent_name({"assigned_to": "x", "last_actor": "y"}) == "x"
        and sessions.agent_name({"last_actor": "y"}) == "y"
        and sessions.agent_name({}) == "",
        "agent_name prioriza assigned_to",
    )
    mcp_created = handlers.call_tool(
        "create",
        {"title": "Creado vía MCP", "agent": "opencode"},
    )
    h.ok(
        mcp_created["assigned_to"] == "opencode",
        "el MCP asigna el agente al crear",
    )
    mcp_anon = handlers.call_tool("create", {"title": "Sin agente MCP"})
    h.ok(
        mcp_anon["assigned_to"] is None,
        "sin agent el MCP no inventa asignación",
    )

    h.ok(
        sessions.agent_color("opencode") == sessions.agent_color("OPENCODE"),
        "el color del agente no depende de mayúsculas",
    )
    h.ok(
        sessions.agent_color("opencode")
        != sessions.agent_color("antigravity"),
        "agentes distintos usan colores distintos",
    )
    h.ok(
        sessions.agent_color(None) == sessions.NO_SESSION_COLOR,
        "sin agente el color es gris",
    )
    h.ok(
        sessions.agent_color("desconocido") in sessions.AGENT_PALETTE
        and sessions.agent_color("desconocido")
        == sessions.agent_color("desconocido"),
        "el color de respaldo es determinista",
    )

    argv = sessions.build_open_command("ses_viva1", directory="/tmp")
    h.ok(
        argv[-4:] == ["-e", "opencode", "--session", "ses_viva1"],
        "el comando abre opencode con la sesión",
    )
    argv_agy = sessions.build_open_command("agy_session_123", directory="/tmp")
    h.ok(
        argv_agy[-4:] == ["-e", "agy", "--conversation", "agy_session_123"],
        "build_open_command abre agy --conversation para sesiones de antigravity",
    )
    h.ok(
        "kitty" in argv[0] or "kitty" in " ".join(argv),
        "el comando usa kitty por defecto",
    )
    h.ok(
        "--directory" in argv and "/tmp" in argv,
        "el comando respeta el directorio de la sesión",
    )
    h.raises(
        sessions.SessionError,
        lambda: sessions.build_open_command("   "),
        "una sesión vacía no se puede abrir",
    )
    h.raises(
        sessions.SessionError,
        lambda: sessions.build_open_command("x", terminal="no-existe-xyz"),
        "una terminal desconocida falla",
    )

    # ── Directorio, terminales y apertura a prueba de fallos ─────
    h.ok(
        sessions.terminal_spec("foot").dir_args == ("--working-directory",)
        and sessions.terminal_spec("/usr/bin/ghostty").dir_args
        == ("--working-directory",)
        and sessions.terminal_spec("xfce4-terminal").dir_args
        == ("--working-directory",),
        "foot, ghostty y xfce4-terminal usan --working-directory",
    )
    h.ok(
        sessions.terminal_spec("konsole").dir_args == ("--workdir",),
        "konsole usa --workdir",
    )
    wez = sessions.terminal_spec("wezterm")
    h.ok(
        wez.dir_args == ("start", "--cwd") and wez.exec_args == ("--",),
        "wezterm usa start --cwd y separa el comando con --",
    )
    h.ok(
        sessions.terminal_spec("terminal-rara") == sessions.GENERIC_TERMINAL,
        "una terminal desconocida usa el respaldo genérico",
    )

    live_dir = str(Path.home() / ".config")
    choice = sessions.resolve_directory(live_dir, str(Path(tmp) / "nada"))
    h.ok(
        choice.path == live_dir
        and choice.source == "sesion"
        and choice.warning is None,
        "resolve_directory prefiere el directorio de la sesión",
    )
    choice = sessions.resolve_directory(str(Path(tmp) / "worktree-borrado"), tmp)
    h.ok(
        choice.path == tmp
        and choice.source == "ticket"
        and "ya no existe" in (choice.warning or ""),
        "si el directorio de la sesión desapareció se usa el del ticket y se avisa",
    )
    choice = sessions.resolve_directory(None, tmp)
    h.ok(
        choice.path == tmp
        and choice.source == "ticket"
        and "no registra directorio" in (choice.warning or ""),
        "una sesión sin directorio avisa de que usa el del ticket",
    )
    h.raises(
        sessions.SessionError,
        lambda: sessions.resolve_directory(None, None),
        "sin ningún directorio válido no se abre",
    )
    h.raises(
        sessions.SessionError,
        lambda: sessions.build_open_command(
            "ses_viva1", directory=str(Path(tmp) / "no-existe")
        ),
        "un directorio inexistente nunca llega al argv",
    )
    h.ok(
        sessions.ensure_session("ses_viva1") is None,
        "ensure_session acepta una sesión viva",
    )
    h.raises(
        sessions.SessionError,
        lambda: sessions.ensure_session("ses_borrada"),
        "no se abre una sesión de OpenCode borrada",
    )
    h.raises(
        sessions.SessionError,
        lambda: sessions.ensure_session("agy_fantasma", backend="antigravity"),
        "no se abre una sesión de Antigravity borrada",
    )

    previous_path = os.environ.get("PATH", "")
    empty_bin = Path(tmp) / "bin-vacio"
    empty_bin.mkdir()
    os.environ["PATH"] = str(empty_bin)
    try:
        h.raises(
            sessions.SessionError,
            lambda: sessions.agent_executable("opencode"),
            "sin el binario del agente en PATH se falla antes de lanzar",
        )
    finally:
        os.environ["PATH"] = previous_path

    fake_bin = Path(tmp) / "bin-falso"
    fake_bin.mkdir()
    fake_agent = fake_bin / "opencode"
    fake_agent.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    fake_agent.chmod(0o755)
    broken_term = fake_bin / "fauxterm"
    broken_term.write_text(
        "#!/bin/sh\necho 'fallo de terminal' >&2\nexit 7\n",
        encoding="utf-8",
    )
    broken_term.chmod(0o755)
    slow_term = fake_bin / "termlenta"
    slow_term.write_text("#!/bin/sh\n/bin/sleep 1\n", encoding="utf-8")
    slow_term.chmod(0o755)
    os.environ["PATH"] = str(fake_bin)
    try:
        try:
            sessions.launch_session(
                "ses_viva1", directory=tmp, terminal=str(broken_term), probe=2.0
            )
        except sessions.SessionError as exc:
            h.ok(
                "fallo de terminal" in str(exc),
                "el error de apertura conserva el stderr de la terminal",
            )
        else:
            raise AssertionError(
                "una terminal que muere al arrancar debe fallar"
            )
        argv_alive = sessions.launch_session(
            "ses_viva1", directory=tmp, terminal=str(slow_term), probe=0.2
        )
        h.ok(
            argv_alive[-1] == "ses_viva1",
            "una terminal que sigue viva no se considera un fallo",
        )
    finally:
        os.environ["PATH"] = previous_path

    h.ok(
        deeplink.scheme_url("T-0001") == "ticket://open/T-0001",
        "scheme_url genera la URL del ticket",
    )
    h.ok(
        deeplink.parse_target("ticket://open/T-0001") == "T-0001"
        and deeplink.parse_target("ticket:T-0001") == "T-0001"
        and deeplink.parse_target("T-0001") == "T-0001",
        "parse_target acepta URL y código",
    )
    h.raises(
        domain.ValidationError,
        lambda: deeplink.parse_target("ticket://borrar/T-0001"),
        "una acción desconocida en la URL falla",
    )
    h.raises(
        domain.ValidationError,
        lambda: deeplink.parse_target("  "),
        "un target vacío falla",
    )

    desktop = deeplink.install_scheme()
    content = desktop.read_text()
    h.ok(
        desktop.exists() and "x-scheme-handler/ticket" in content,
        "scheme-install crea el handler de escritorio",
    )
    h.ok("taiki open %u" in content, "el handler ejecuta taiki open")
    h.ok(deeplink.scheme_installed(), "scheme_installed detecta el handler")
    h.ok(deeplink.uninstall_scheme(), "scheme-uninstall elimina el handler")
    h.ok(
        not deeplink.scheme_installed(),
        "tras desinstalar no queda handler",
    )

    live = repo.get_ticket(conn, t_live["code"])
    colored = render.render_table(
        [live], use_color=True, session_map={live["code"]: "ses_viva1"}
    )
    h.ok(
        f"\x1b]8;;ticket://open/{live['code']}" in colored,
        "la tabla enlaza el agente con sesión viva",
    )
    h.ok("\x1b[39m" in colored, "el agente usa su color propio")
    grey = render.render_table(
        [live], use_color=True, session_map={live["code"]: None}
    )
    h.ok(
        "\x1b[244m" in grey and "]8;;" not in grey,
        "sin sesión el agente va en gris y sin enlace",
    )
    plain = render.render_table(
        [live], use_color=False, session_map={live["code"]: "ses_viva1"}
    )
    h.ok("\x1b" not in plain, "sin color no hay secuencias de escape")
