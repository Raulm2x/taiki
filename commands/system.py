"""Comandos de sistema: sesiones, enlaces de escritorio y TUI."""

from __future__ import annotations

import argparse
import sys

from ticketlib import db, deeplink, herdr, repo, sessions, workspace

from .common import _conn


def _herdr_open(conn, args, ticket, session_id, info, choice) -> int:
    """Abre (o enfoca) el ticket en herdr y enlaza la sesión nueva."""
    try:
        result = herdr.open_and_link(
            conn,
            ticket,
            session_id,
            backend=info.get("backend"),
            directory=choice.path,
            force_new=bool(getattr(args, "new", False)),
            space=getattr(args, "space", "auto"),
            focus=not getattr(args, "no_focus", False),
            dry_run=bool(args.print_only),
        )
    except herdr.HerdrError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    if args.print_only:
        for step in result["steps"]:
            print("herdr " + " ".join(step))
        return 0
    if result["action"] == "focus":
        print(f"{ticket['code']} ya está abierto en herdr ({result['pane_id']})")
        return 0
    message = (
        f"{ticket['code']} abierto en herdr · pane {result['pane_id']}"
    )
    if result.get("session_id"):
        message += f" · sesión {result['session_id']}"
    print(message)
    return 0


def cmd_open(args: argparse.Namespace) -> int:
    code = deeplink.parse_target(args.target)
    force = bool(getattr(args, "herdr", False))
    disabled = bool(getattr(args, "no_herdr", False))
    if force and not herdr.available():
        print(
            "Error: herdr no está disponible (HERDR_ENV=1 y binario en PATH)",
            file=sys.stderr,
        )
        return 1
    use_herdr = force or (herdr.available() and not disabled)

    with _conn() as conn:
        ticket = repo.get_ticket(conn, code)
        session_id = sessions.ticket_sessions(conn, [ticket["code"]])[
            ticket["code"]
        ]
        info = (
            sessions.session_info([session_id]).get(session_id, {})
            if session_id
            else {}
        )
        try:
            choice = sessions.resolve_directory(
                info.get("directory"), ticket.get("path")
            )
        except sessions.SessionError as exc:
            print(f"Error: {exc}", file=sys.stderr)
            return 1

        if choice.warning:
            print(f"Aviso: {choice.warning}", file=sys.stderr)

        if use_herdr:
            return _herdr_open(conn, args, ticket, session_id, info, choice)

        if not session_id:
            print(
                f"{ticket['code']} no tiene una sesión viva que abrir",
                file=sys.stderr,
            )
            return 1

        backend = info.get("backend")
        try:
            argv = sessions.build_open_command(
                session_id,
                directory=choice.path,
                terminal=args.terminal,
                backend=backend,
            )
            if not args.print_only:
                sessions.launch_session(
                    session_id,
                    directory=choice.path,
                    terminal=args.terminal,
                    backend=backend,
                )
        except sessions.SessionError as exc:
            print(f"Error: {exc}", file=sys.stderr)
            return 1

        if args.print_only:
            print(" ".join(argv))
        else:
            backend_name = info.get("backend") or "agente"
            print(
                f"Abriendo {ticket['code']} en la sesión de {backend_name} "
                f"({session_id}) en {choice.path}"
            )
    return 0

def cmd_scheme_install(args: argparse.Namespace) -> int:
    path = deeplink.install_scheme()
    print(f"Handler ticket:// registrado: {path}")
    print("Ctrl+clic sobre AGENTE en `ticket list` abrirá la sesión.")
    return 0


def cmd_scheme_uninstall(args: argparse.Namespace) -> int:
    if deeplink.uninstall_scheme():
        print("Handler ticket:// eliminado")
    else:
        print("No había handler ticket:// instalado")
    return 0


def cmd_tui(args: argparse.Namespace) -> int:
    if not sys.stdout.isatty():
        print("La TUI requiere una terminal interactiva", file=sys.stderr)
        return 1
    info = workspace.resolve()
    if not info["exists"]:
        print(
            "No hay una base de datos Taiki en este proyecto.\n"
            "Crea una con: ticket init --workspace",
            file=sys.stderr,
        )
        return 1
    config = workspace.load_config()
    refresh = args.refresh
    if refresh is None:
        refresh = (config.get("ui") or {}).get("refresh", 2.0)
    import tui

    return tui.run(
        statuses=args.status,
        project=args.project,
        query=args.query,
        limit=args.limit,
        refresh=refresh,
        order=args.order,
    )
