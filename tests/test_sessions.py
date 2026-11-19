"""Sesiones de agente, enlaces y terminales."""

from __future__ import annotations

import json
import os
import sqlite3
from pathlib import Path
from mcp_layer import handlers
from ticketlib import deeplink, domain, render, repo, sessions
from tests.support import Harness


def _fails(exc_type, call) -> bool:
    """True si ``call`` lanza ``exc_type`` (tabla de aserciones)."""
    try:
        call()
    except exc_type:
        return True
    return False


def run(h: Harness) -> None:
    conn = h.conn
    tmp = str(h.tmp)
    live_dir = str(Path(tmp) / "live-config")
    Path(live_dir).mkdir()

    # ── Sesiones de agente, enlaces ticket:// y TUI ─────────────────────
    oc_db = Path(tmp) / "opencode.db"
    oc = sqlite3.connect(oc_db)
    oc.execute(
        "CREATE TABLE session_v2 (id TEXT PRIMARY KEY, directory TEXT, title TEXT)"
    )
    oc.execute(
        "INSERT INTO session_v2 VALUES ('ses_viva1', ?, 'viva')",
        (live_dir,),
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

    agy_info = sessions.session_info(["agy_session_123"])["agy_session_123"]
    h.eq(
        (
            sessions.opencode_db_path(),
            sessions.existing_sessions(
                ["ses_viva1", "agy_session_123", "ses_fantasma"]
            ),
            (agy_info["backend"], agy_info["title"]),
            sessions.session_info(["ses_viva1"])["ses_viva1"]["directory"],
            sessions.existing_sessions([]),
        ),
        (
            oc_db,
            {"ses_viva1", "agy_session_123"},
            ("antigravity", "Prueba Antigravity T-0005"),
            live_dir,
            set(),
        ),
        "OPENCODE_DB sobrescribe la ruta, existing_sessions descarta las "
        "borradas e incluye Antigravity y session_info resuelve metadatos "
        "y directorio",
    )

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
    t_link = repo.create_ticket(conn, title="Solo enlace de sesión")
    repo.link_ticket(conn, t_link["code"], "sesion", "ses_viva1")
    link_session = sessions.ticket_sessions(conn, [t_link["code"]])
    h.eq(
        (
            resolved[t_live["code"]],
            resolved[t_dead["code"]],
            link_session[t_link["code"]],
        ),
        ("ses_viva1", "ses_viva2", "ses_viva1"),
        "ticket_sessions encuentra la sesión viva, retrocede a la anterior "
        "y el enlace 'sesion' sirve de respaldo",
    )

    # Agente automático: sin claim, el actor del último evento manda
    t_unclaimed = repo.create_ticket(
        conn,
        title="Creado sin reclamar",
        assigned_to="opencode",
        session_id="ses_viva1",
    )
    h.eq(
        (
            t_unclaimed["assigned_to"],
            sessions.ticket_sessions(conn, [t_unclaimed["code"]])[
                t_unclaimed["code"]
            ],
            repo.get_ticket(conn, t_unclaimed["code"])["assigned_to"],
        ),
        ("opencode", "ses_viva1", "opencode"),
        "create_ticket guarda el agente que lo crea y un ticket recién "
        "creado ya tiene sesión y agente",
    )

    t_bare = repo.create_ticket(conn, title="Sin agente")
    repo.comment_ticket(
        conn, t_bare["code"], "nota", author="codex", session_id="ses_viva2"
    )
    activity = sessions.ticket_activity(
        conn, [t_bare["code"], t_live["code"]]
    )
    repo.link_ticket(
        conn,
        t_bare["code"],
        "commit",
        "abc9999",
        actor="agente",
    )
    latest = sessions.ticket_activity(conn, [t_bare["code"]])
    sessions.decorate([t_bare], activity)
    h.eq(
        (
            (
                activity[t_bare["code"]]["actor"],
                activity[t_bare["code"]]["session"],
            ),
            (
                latest[t_bare["code"]]["actor"],
                latest[t_bare["code"]]["session"],
            ),
            (
                t_bare["last_actor"],
                "last_actor" not in t_live or bool(t_live.get("assigned_to")),
            ),
        ),
        (
            ("codex", "ses_viva2"),
            ("codex", "ses_viva2"),
            ("codex", True),
        ),
        "ticket_activity devuelve el último actor y su sesión viva, un "
        "evento genérico del hook no lo tapa y decorate rellena "
        "last_actor sin pisar el assigned_to existente",
    )

    fallback_table = render.render_table(
        [t_bare],
        use_color=True,
        session_map={t_bare["code"]: "ses_viva2"},
    )
    mcp_created = handlers.call_tool(
        "create",
        {"title": "Creado vía MCP", "agent": "opencode"},
    )
    mcp_anon = handlers.call_tool("create", {"title": "Sin agente MCP"})
    h.eq(
        (
            "codex" in fallback_table
            and f"ticket://open/{t_bare['code']}" in fallback_table,
            (
                sessions.agent_name({"assigned_to": "x", "last_actor": "y"}),
                sessions.agent_name({"last_actor": "y"}),
                sessions.agent_name({}),
            ),
            (mcp_created["assigned_to"], mcp_anon.get("assigned_to")),
        ),
        (
            True,
            ("x", "y", ""),
            ("opencode", None),
        ),
        "la tabla enlaza al agente de respaldo, agent_name prioriza "
        "assigned_to y el MCP solo asigna el agente cuando se pide",
    )

    h.eq(
        (
            sessions.agent_color("opencode") == sessions.agent_color("OPENCODE"),
            sessions.agent_color("opencode") != sessions.agent_color("antigravity"),
            sessions.agent_color(None) == sessions.NO_SESSION_COLOR,
            sessions.agent_color("desconocido") in sessions.AGENT_PALETTE
            and sessions.agent_color("desconocido")
            == sessions.agent_color("desconocido"),
        ),
        (True, True, True, True),
        "el color del agente no depende de mayúsculas, distingue agentes, "
        "es gris sin agente y el respaldo es determinista",
    )

    # La terminal por defecto debe existir en PATH: en CI no hay kitty, así
    # que el test crea una falsa en vez de depender de la máquina.
    fake_bin = Path(tmp) / "fake-bin"
    fake_bin.mkdir()
    fake_kitty = fake_bin / "kitty"
    fake_kitty.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    fake_kitty.chmod(0o755)
    with h.env(PATH=f"{fake_bin}{os.pathsep}{os.environ.get('PATH', '')}"):
        argv = sessions.build_open_command("ses_viva1", directory="/tmp")
        argv_agy = sessions.build_open_command("agy_session_123", directory="/tmp")
        h.eq(
            (
                argv[-4:],
                argv_agy[-4:],
                "kitty" in argv[0] or "kitty" in " ".join(argv),
                "--directory" in argv and "/tmp" in argv,
                _fails(
                    sessions.SessionError,
                    lambda: sessions.build_open_command("   "),
                ),
                _fails(
                    sessions.SessionError,
                    lambda: sessions.build_open_command(
                        "x", terminal="no-existe-xyz"
                    ),
                ),
            ),
            (
                ["-e", "opencode", "--session", "ses_viva1"],
                ["-e", "agy", "--conversation", "agy_session_123"],
                True,
                True,
                True,
                True,
            ),
            "el comando abre opencode con la sesión y agy --conversation "
            "para Antigravity, usa kitty por defecto, respeta el directorio "
            "y rechaza sesiones vacías o terminales desconocidas",
        )

    # ── Directorio, terminales y apertura a prueba de fallos ─────
    wez = sessions.terminal_spec("wezterm")
    choice_live = sessions.resolve_directory(live_dir, str(Path(tmp) / "nada"))
    choice_gone = sessions.resolve_directory(
        str(Path(tmp) / "worktree-borrado"), tmp
    )
    choice_none = sessions.resolve_directory(None, tmp)
    h.eq(
        (
            (
                sessions.terminal_spec("foot").dir_args,
                sessions.terminal_spec("/usr/bin/ghostty").dir_args,
                sessions.terminal_spec("xfce4-terminal").dir_args,
            ),
            sessions.terminal_spec("konsole").dir_args,
            (wez.dir_args, wez.exec_args),
            sessions.terminal_spec("terminal-rara")
            == sessions.GENERIC_TERMINAL,
            (
                choice_live.path,
                choice_live.source,
                choice_live.warning,
            ),
            (
                choice_gone.path,
                choice_gone.source,
                "ya no existe" in (choice_gone.warning or ""),
            ),
            (
                choice_none.path,
                choice_none.source,
                "no registra directorio" in (choice_none.warning or ""),
            ),
            _fails(
                sessions.SessionError,
                lambda: sessions.resolve_directory(None, None),
            ),
        ),
        (
            (
                ("--working-directory",),
                ("--working-directory",),
                ("--working-directory",),
            ),
            ("--workdir",),
            (("start", "--cwd"), ("--",)),
            True,
            (live_dir, "sesion", None),
            (tmp, "ticket", True),
            (tmp, "ticket", True),
            True,
        ),
        "foot, ghostty y xfce4-terminal usan --working-directory, konsole "
        "--workdir, wezterm start --cwd y el respaldo genérico cubre lo "
        "desconocido; resolve_directory prefiere la sesión, avisa si "
        "desapareció o no registra y falla sin ningún directorio válido",
    )
    h.eq(
        (
            _fails(
                sessions.SessionError,
                lambda: sessions.build_open_command(
                    "ses_viva1", directory=str(Path(tmp) / "no-existe")
                ),
            ),
            sessions.ensure_session("ses_viva1"),
            _fails(
                sessions.SessionError,
                lambda: sessions.ensure_session("ses_borrada"),
            ),
            _fails(
                sessions.SessionError,
                lambda: sessions.ensure_session(
                    "agy_fantasma", backend="antigravity"
                ),
            ),
        ),
        (True, None, True, True),
        "un directorio inexistente nunca llega al argv, ensure_session "
        "acepta una sesión viva y rechaza las borradas de ambos backends",
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
            "ses_viva1", directory=tmp, terminal=str(slow_term), probe=0.05
        )
        h.ok(
            argv_alive[-1] == "ses_viva1",
            "una terminal que sigue viva no se considera un fallo",
        )
    finally:
        os.environ["PATH"] = previous_path

    h.eq(
        (
            deeplink.scheme_url("T-0001"),
            (
                deeplink.parse_target("ticket://open/T-0001"),
                deeplink.parse_target("ticket:T-0001"),
                deeplink.parse_target("T-0001"),
            ),
            _fails(
                domain.ValidationError,
                lambda: deeplink.parse_target("ticket://borrar/T-0001"),
            ),
            _fails(
                domain.ValidationError,
                lambda: deeplink.parse_target("  "),
            ),
        ),
        (
            "ticket://open/T-0001",
            ("T-0001", "T-0001", "T-0001"),
            True,
            True,
        ),
        "scheme_url genera la URL del ticket, parse_target acepta URL y "
        "código y rechaza acciones desconocidas o targets vacíos",
    )

    desktop = deeplink.install_scheme()
    content = desktop.read_text()
    handler_ok = (
        desktop.exists() and "x-scheme-handler/ticket" in content
    )
    installed = deeplink.scheme_installed()
    uninstalled = deeplink.uninstall_scheme()
    h.eq(
        (
            handler_ok,
            "taiki open %u" in content,
            installed,
            uninstalled,
            deeplink.scheme_installed(),
        ),
        (True, True, True, True, False),
        "scheme-install crea el handler de escritorio que ejecuta taiki "
        "open %u, scheme_installed lo detecta y uninstall lo elimina",
    )

    live = repo.get_ticket(conn, t_live["code"])
    colored = render.render_table(
        [live], use_color=True, session_map={live["code"]: "ses_viva1"}
    )
    grey = render.render_table(
        [live], use_color=True, session_map={live["code"]: None}
    )
    plain = render.render_table(
        [live], use_color=False, session_map={live["code"]: "ses_viva1"}
    )
    h.eq(
        (
            f"\x1b]8;;ticket://open/{live['code']}" in colored,
            "\x1b[39m" in colored,
            "\x1b[244m" in grey and "]8;;" not in grey,
            "\x1b" not in plain,
        ),
        (True, True, True, True),
        "la tabla enlaza el agente con sesión viva en su color, sin sesión "
        "va en gris y sin enlace y sin color no hay secuencias de escape",
    )
