#!/usr/bin/env python3
"""Parser de argumentos y punto de entrada del CLI de tickets."""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from commands import (
    cmd_attach,
    cmd_audit,
    cmd_auto_link,
    cmd_claim,
    cmd_comment,
    cmd_commit,
    cmd_complete,
    cmd_detach,
    cmd_confirm,
    cmd_export,
    cmd_guard,
    cmd_hook_check_msg,
    cmd_hook_check_index,
    cmd_hook_check_push,
    cmd_heartbeat,
    cmd_hook_install,
    cmd_init,
    cmd_link,
    cmd_list,
    cmd_mcp_install,
    cmd_mcp_uninstall,
    cmd_new,
    cmd_next,
    cmd_open,
    cmd_scratch,
    cmd_overview,
    cmd_renumber,
    cmd_repair_gaps,
    cmd_reject,
    cmd_reopen,
    cmd_scheme_install,
    cmd_servers,
    cmd_scheme_uninstall,
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
from ticketlib import domain
from ticketlib.domain import KINDS, LINK_KINDS, PRIORITIES, STATUSES


# ── Parser ───────────────────────────────────────────────────────────────────


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="taiki", description="Sistema de tickets local"
    )
    parser.add_argument("--db", help="Ruta alternativa de la base de datos")
    parser.set_defaults(func=cmd_overview)
    sub = parser.add_subparsers(dest="command", required=False)

    p = sub.add_parser("list", aliases=["ls"], help="Lista tickets")
    p.add_argument("-s", "--status", action="append", choices=STATUSES)
    p.add_argument("-k", "--kind", choices=KINDS)
    p.add_argument("-p", "--priority", choices=PRIORITIES)
    p.add_argument("--project")
    p.add_argument("--assignee")
    p.add_argument("-q", "--query")
    p.add_argument("--tag", help="Filtra por etiqueta")
    p.add_argument("--all", action="store_true", help="Incluye estados finales")
    group = p.add_mutually_exclusive_group()
    group.add_argument(
        "--flat",
        action="store_true",
        help="Muestra también las tareas de cada épica",
    )
    group.add_argument(
        "--tree",
        action="store_true",
        help="Árbol de épicas y tareas (sin paginar)",
    )
    p.add_argument("--parent", help="Muestra solo las tareas de esta épica")
    p.add_argument("--archived", action="store_true", help="Incluye archivados")
    p.add_argument(
        "--include-scratch",
        action="store_true",
        help="Incluye los tickets temporales (scratch), ocultos por defecto",
    )
    p.add_argument("--limit", type=int, default=50)
    p.add_argument("--offset", type=int, default=0, help="Salta N tickets")
    p.add_argument(
        "--sort",
        choices=("code", "priority"),
        default="code",
        help="Orden: código descendente (defecto) o prioridad",
    )
    p.add_argument("--json", action="store_true", help="Emite JSON")
    p.add_argument(
        "--json-summary",
        action="store_true",
        help="Emite el resumen lean sin historial (implica --json)",
    )
    p.add_argument(
        "--compact",
        action="store_true",
        help="Emite JSON compacto sin sangría (implica --json)",
    )
    p.set_defaults(func=cmd_list)

    p = sub.add_parser("attach", help="Agrupa tickets bajo una épica")
    p.add_argument("parent")
    p.add_argument("codes", nargs="+")
    p.add_argument("--agent", default="usuario")
    p.set_defaults(func=cmd_attach)

    p = sub.add_parser("detach", help="Saca tickets de su épica")
    p.add_argument("codes", nargs="+")
    p.add_argument("--agent", default="usuario")
    p.set_defaults(func=cmd_detach)

    p = sub.add_parser("show", help="Muestra un ticket")
    p.add_argument("code")
    p.add_argument("--json", action="store_true", help="Emite JSON")
    p.add_argument(
        "--compact",
        action="store_true",
        help="Emite JSON compacto sin sangría (implica --json)",
    )
    p.set_defaults(func=cmd_show)

    p = sub.add_parser("new", aliases=["add"], help="Crea un ticket")
    p.add_argument("-t", "--title")
    p.add_argument("-b", "--body")
    p.add_argument("-k", "--kind", choices=KINDS)
    p.add_argument("-p", "--priority", choices=PRIORITIES)
    p.add_argument("--project")
    p.add_argument("--path")
    p.add_argument("--tags", nargs="*")
    p.add_argument(
        "--paths",
        nargs="+",
        help="rutas o globs que posee el ticket (para commits seguros)",
    )
    p.add_argument("--parent")
    p.set_defaults(func=cmd_new)

    p = sub.add_parser(
        "repair-gaps",
        help="Cubre huecos de numeración con tickets cancelados",
        description="Crea un ticket genérico cancelado para cada T-XXXX que "
        "nunca existió, de forma que la secuencia quede completa. Simula por "
        "defecto; los números cubiertos no se reutilizan.",
    )
    p.add_argument("--apply", action="store_true", help="Aplica los cambios")
    p.add_argument("--agent", help="Agente que ejecuta el cambio")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_repair_gaps)

    p = sub.add_parser(
        "renumber",
        help="Renombra tareas con código plano a T-XXXX.NN",
        description="Detecta hijos creados con el padre pero con código normal "
        "(procesos antiguos) y los renombra; los adoptados con attach no se tocan.",
    )
    p.add_argument("--apply", action="store_true", help="Aplica los cambios")
    p.add_argument("--agent", help="Agente que ejecuta el cambio")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_renumber)

    p = sub.add_parser("claim", help="Reclama un ticket")
    p.add_argument("code")
    p.add_argument("--agent", default="usuario")
    p.add_argument("--force", action="store_true", help="ignora asignación y sesión")
    p.add_argument(
        "--steal",
        action="store_true",
        help="recupera un claim caducado de otra sesión",
    )
    p.add_argument(
        "--worktree",
        action="store_true",
        help="crea y enlaza un worktree para el ticket",
    )
    p.set_defaults(func=cmd_claim)

    p = sub.add_parser(
        "heartbeat",
        help="Renueva el lease de un ticket activo (comandos largos)",
    )
    p.add_argument("code", nargs="?", help="Código del ticket (o el único activo)")
    p.add_argument(
        "--minutes", type=int, help="Minutos de lease (por defecto TAIKI_LEASE_MINUTES)"
    )
    p.set_defaults(func=cmd_heartbeat)

    p = sub.add_parser("update", help="Actualiza campos")
    p.add_argument("code")
    p.add_argument("-t", "--title")
    p.add_argument("-b", "--body")
    p.add_argument("-k", "--kind", choices=KINDS)
    p.add_argument("-p", "--priority", choices=PRIORITIES)
    p.add_argument("--project")
    p.add_argument("--path")
    p.add_argument(
        "--paths",
        nargs="+",
        help="rutas o globs que posee el ticket",
    )
    p.add_argument("--tags", nargs="*")
    p.set_defaults(func=cmd_update)

    p = sub.add_parser("transition", help="Cambia el estado")
    p.add_argument("code")
    p.add_argument("status", choices=STATUSES)
    p.add_argument("--note")
    p.add_argument(
        "--force",
        action="store_true",
        help="Cambia el estado aunque el ticket esté reclamado",
    )
    p.set_defaults(func=cmd_transition)

    p = sub.add_parser(
        "confirm",
        help="Confirma uno o varios tickets completados",
        description="Confirma varios tickets de forma atómica: si alguno no "
        "está en 'completado' no se confirma ninguno.",
    )
    p.add_argument("code", nargs="+", help="Uno o varios códigos (T-0001 T-0002)")
    p.add_argument("--note")
    p.add_argument(
        "--keep-worktrees",
        action="store_true",
        help="No retirar los worktrees limpios y mergeados",
    )
    p.set_defaults(func=cmd_confirm)

    p = sub.add_parser(
        "reject",
        help="Rechaza un ticket completado y lo devuelve a en_progreso",
    )
    p.add_argument("code")
    p.add_argument("--why", required=True, help="Motivo del rechazo")
    p.set_defaults(func=cmd_reject)

    p = sub.add_parser("reopen", help="Reabre un ticket")
    p.add_argument("code")
    p.add_argument("--why", required=True)
    p.set_defaults(func=cmd_reopen)

    p = sub.add_parser("complete", help="Completa un ticket")
    p.add_argument("code")
    p.add_argument("-r", "--resolution", required=True)
    p.add_argument("--commit")
    p.add_argument("--file", action="append")
    p.add_argument(
        "--force",
        action="store_true",
        help="Completa aunque el ticket esté reclamado por otra sesión",
    )
    p.set_defaults(func=cmd_complete)

    p = sub.add_parser("comment", help="Añade un comentario")
    p.add_argument("code")
    p.add_argument("body", nargs="*")
    p.set_defaults(func=cmd_comment)

    p = sub.add_parser(
        "scratch",
        help="Ticket temporal para pruebas (oculto de la tabla)",
        description="Crea o reutiliza un ticket temporal con caducidad, oculto "
        "de list/stats/next/TUI/audit. Sirve para trabajar sin ensuciar la "
        "tabla; el guard lo acepta como ticket activo.",
    )
    p.add_argument("-t", "--title", help="Título del ticket temporal")
    p.add_argument(
        "--ttl",
        type=int,
        default=120,
        help="Minutos de vida antes de archivarse (por defecto 120)",
    )
    p.add_argument("--agent", default="opencode", help="Nombre del agente")
    action = p.add_mutually_exclusive_group()
    action.add_argument(
        "--list", action="store_true", help="Lista los scratch vivos"
    )
    action.add_argument(
        "--clean", action="store_true", help="Archiva los scratch caducados"
    )
    action.add_argument("--drop", metavar="CODIGO", help="Archiva un scratch")
    action.add_argument(
        "--promote",
        metavar="CODIGO",
        help="Quita la marca scratch y lo devuelve a la tabla",
    )
    p.set_defaults(func=cmd_scratch)

    p = sub.add_parser("stats", help="Resumen de tickets")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_stats)

    p = sub.add_parser("next", help="Siguiente ticket pendiente")
    p.add_argument("--project")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_next)

    p = sub.add_parser("export", help="Exporta todos los tickets a JSON")
    p.add_argument("-o", "--output")
    p.set_defaults(func=cmd_export)

    p = sub.add_parser(
        "init",
        help="Inicializa un workspace .taiki o muestra la base actual",
    )
    p.add_argument(
        "-w",
        "--workspace",
        action="store_true",
        help="crea .taiki/ en la raíz del proyecto",
    )
    p.add_argument("--adopt", metavar="DB", help="adopta una base de datos existente")
    p.add_argument("--root", help="proyecto donde crear .taiki/")
    p.add_argument("--name", help="nombre legible del proyecto")
    p.add_argument(
        "--force",
        action="store_true",
        help="reemplaza taiki.db al adoptar (guarda copia)",
    )
    p.add_argument(
        "--mcp",
        action="store_true",
        help="instala además el MCP en este proyecto (OpenCode)",
    )
    p.set_defaults(func=cmd_init)

    p = sub.add_parser("mcp", help="Instala o retira el MCP de este proyecto")
    mcp_sub = p.add_subparsers(dest="mcp_action", required=True)
    mcp_add = mcp_sub.add_parser(
        "install", help="registra el MCP solo en el proyecto actual"
    )
    mcp_add.add_argument(
        "--antigravity",
        action="store_true",
        help="también en Antigravity (su configuración es global)",
    )
    mcp_add.set_defaults(func=cmd_mcp_install)
    mcp_rm = mcp_sub.add_parser(
        "uninstall", help="retira el MCP del proyecto actual"
    )
    mcp_rm.add_argument(
        "--antigravity",
        action="store_true",
        help="también de Antigravity (su configuración es global)",
    )
    mcp_rm.set_defaults(func=cmd_mcp_uninstall)

    p = sub.add_parser("worktree", help="Worktrees por tarea (aislamiento)")
    wt_sub = p.add_subparsers(dest="worktree_action", required=True)
    wt_create = wt_sub.add_parser(
        "create", help="crea un worktree para un ticket"
    )
    wt_create.add_argument("code")
    wt_create.add_argument("--branch", help="rama a crear (por defecto agent/T-XXXX-slug)")
    wt_create.add_argument("--path", help="ruta alternativa del worktree")
    wt_create.add_argument("--from", dest="start", default="HEAD", help="referencia base")
    wt_create.add_argument("--force", action="store_true", help="permite otro worktree")
    wt_create.add_argument("--json", action="store_true")
    wt_create.set_defaults(func=cmd_worktree_create)
    wt_list = wt_sub.add_parser("list", help="lista worktrees de tareas")
    wt_list.add_argument("--json", action="store_true")
    wt_list.set_defaults(func=cmd_worktree_list)
    wt_rm = wt_sub.add_parser("remove", help="retira un worktree")
    wt_rm.add_argument("code", nargs="?")
    wt_rm.add_argument("--path", help="ruta concreta del worktree")
    wt_rm.add_argument("--force", action="store_true", help="descarta cambios")
    wt_rm.add_argument(
        "--delete-branch",
        action="store_true",
        help="elimina también la rama del agente",
    )
    wt_rm.add_argument("--json", action="store_true")
    wt_rm.set_defaults(func=cmd_worktree_remove)
    wt_adopt = wt_sub.add_parser(
        "adopt", help="registra un worktree existente (por ejemplo de OpenCode)"
    )
    wt_adopt.add_argument("code")
    wt_adopt.add_argument("path")
    wt_adopt.add_argument("--force", action="store_true")
    wt_adopt.add_argument("--json", action="store_true")
    wt_adopt.set_defaults(func=cmd_worktree_adopt)
    wt_merge = wt_sub.add_parser(
        "merge", help="rebase, tests y merge ff-only del worktree"
    )
    wt_merge.add_argument("code", nargs="?")
    wt_merge.add_argument("--path", help="ruta concreta del worktree")
    wt_merge.add_argument("--no-tests", action="store_true")
    wt_merge.add_argument(
        "--delete", action="store_true", help="retira el worktree tras mergear"
    )
    wt_merge.add_argument("--json", action="store_true")
    wt_merge.set_defaults(func=cmd_worktree_merge)

    p = sub.add_parser("link", help="Enlaza una referencia a un ticket")
    p.add_argument("code")
    p.add_argument("kind", choices=LINK_KINDS)
    p.add_argument("ref")
    p.set_defaults(func=cmd_link)

    p = sub.add_parser(
        "guard",
        help="Comprueba si hay un ticket activo antes de escribir",
        description="Pensado para agentes: si no hay ticket en progreso, "
        "reutiliza el pendiente de la sesión o crea uno y lo reclama.",
    )
    p.add_argument("--session", help="Identificador de la sesión del agente")
    p.add_argument("--agent", default="opencode", help="Nombre del agente")
    p.add_argument(
        "--tool",
        default="bash",
        help="Herramienta que se va a ejecutar (write, edit, bash…)",
    )
    p.add_argument("--path", help="Fichero que se va a modificar")
    p.add_argument("--command", help="Comando de shell que se va a ejecutar")
    p.add_argument("--cwd", help="Directorio de trabajo del agente")
    p.add_argument(
        "--no-auto",
        action="store_true",
        help="No crea ticket automáticamente: solo bloquea",
    )
    p.add_argument(
        "--hook",
        action="store_true",
        help="Modo hook genérico: lee JSON por stdin y responde allow/deny",
    )
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_guard)

    p = sub.add_parser(
        "auto-link", help="Enlaza el commit a los tickets mencionados en su mensaje"
    )
    p.add_argument("--commit", help="Commit a enlazar (por defecto HEAD)")
    p.add_argument("--message", help="Mensaje a analizar en vez de leerlo de git")
    p.add_argument("--repo", help="Ruta del repositorio git")
    p.add_argument("--quiet", action="store_true")
    p.set_defaults(func=cmd_auto_link)

    p = sub.add_parser(
        "hook-install",
        help="Instala el post-commit y el commit-msg de tickets en el repo",
    )
    p.add_argument("--repo", help="Ruta del repositorio git")
    p.set_defaults(func=cmd_hook_install)

    p = sub.add_parser(
        "hook-check-msg",
        help="Valida un mensaje de commit (lo usa el hook commit-msg)",
    )
    p.add_argument("file", help="Fichero con el mensaje de commit")
    p.set_defaults(func=cmd_hook_check_msg)

    p = sub.add_parser(
        "hook-check-index",
        help="Valida el índice contra el ticket (lo usa el hook pre-commit)",
    )
    p.add_argument("--ticket", help="Código del ticket (o TAIKI_TICKET)")
    p.set_defaults(func=cmd_hook_check_index)

    p = sub.add_parser(
        "status",
        help="Sesiones activas, claims, worktrees y solapamientos",
    )
    p.add_argument("--json", action="store_true")
    p.add_argument(
        "--sessions", action="store_true", help="Solo la tabla de sesiones"
    )
    p.add_argument(
        "--window",
        type=int,
        default=600,
        help="Segundos para considerar reciente un latido",
    )
    p.add_argument(
        "--no-servers", action="store_true", help="No consultar el inventario MCP"
    )
    p.set_defaults(func=cmd_status)

    p = sub.add_parser(
        "commit",
        help="Commitea solo las rutas del ticket activo",
        description="Rechaza ficheros stageados fuera del alcance del ticket "
        "o que solapen con otro ticket activo.",
    )
    p.add_argument("-m", "--message", help="Mensaje del commit")
    p.add_argument("-t", "--ticket", help="Código del ticket (o TAIKI_TICKET)")
    p.add_argument("--dry-run", action="store_true", help="Solo comprueba")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_commit)

    p = sub.add_parser(
        "servers",
        help="Inventario de servidores MCP y limpieza de huérfanos",
        description="Lista los procesos del servidor MCP con su estado (al día, "
        "desactualizado, huérfano). Un proceso con padre vivo nunca se toca, "
        "aunque la sesión esté idle.",
    )
    p.add_argument("--prune", action="store_true", help="Prepara la limpieza")
    p.add_argument(
        "--apply", action="store_true", help="Termina los huérfanos de verdad"
    )
    p.add_argument(
        "--duplicates",
        action="store_true",
        help="Incluye duplicados en la limpieza (conserva el más reciente)",
    )
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_servers)

    p = sub.add_parser(
        "hook-check-push",
        help="Bloquea el push si hay tickets sin confirmar (lo usa el hook)",
    )
    p.add_argument("--repo", help="Ruta del repositorio git")
    # git pasa el remoto y la URL como argumentos; se ignoran.
    p.add_argument("remote", nargs="?")
    p.add_argument("url", nargs="?")
    p.set_defaults(func=cmd_hook_check_push)

    p = sub.add_parser(
        "audit",
        help="Audita commits sin ticket y tickets estancados",
    )
    p.add_argument("--repo", help="Ruta del repositorio git")
    p.add_argument("--days", type=int, default=7, help="Ventana de commits")
    p.add_argument("--limit", type=int, default=100, help="Commits a revisar")
    p.add_argument(
        "--stalled-days",
        type=int,
        default=2,
        help="Días sin actividad para considerar estancado un ticket",
    )
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_audit)

    p = sub.add_parser(
        "open",
        help="Abre (o enfoca) el agente del ticket, en herdr si está disponible",
        description="Abre el ticket o la URL ticket://open/T-0001. Dentro de "
        "herdr enfoca la sesión viva o abre una pestaña con el agente; fuera "
        "de herdr lanza una terminal con `opencode --session` o "
        "`agy --conversation`.",
    )
    p.add_argument("target", help="Código (T-0001) o URL ticket://open/T-0001")
    p.add_argument(
        "--terminal",
        help="Terminal a usar (por defecto kitty; admite alacritty, foot, "
        "ghostty, konsole, xfce4-terminal, gnome-terminal y wezterm)",
    )
    p.add_argument(
        "--print-only", action="store_true", help="Solo muestra el plan o el comando"
    )
    group = p.add_mutually_exclusive_group()
    group.add_argument(
        "--herdr", action="store_true", help="Abre en herdr (aunque no se detecte)"
    )
    group.add_argument(
        "--no-herdr", action="store_true", help="No usa herdr aunque esté disponible"
    )
    p.add_argument(
        "--new", action="store_true", help="Ignora la sesión viva y abre otra"
    )
    p.add_argument(
        "--space",
        choices=("auto", "current"),
        default="auto",
        help="Workspace: por directorio del ticket (auto) o el actual",
    )
    p.add_argument(
        "--no-focus",
        action="store_true",
        help="No cambia el foco de herdr al abrir",
    )
    p.set_defaults(func=cmd_open)

    p = sub.add_parser(
        "scheme-install",
        help="Registra el handler ticket:// para Ctrl+clic en la lista",
    )
    p.set_defaults(func=cmd_scheme_install)

    p = sub.add_parser(
        "scheme-uninstall", help="Elimina el handler ticket://"
    )
    p.set_defaults(func=cmd_scheme_uninstall)

    p = sub.add_parser(
        "tui",
        help="Abre la TUI para revisar y confirmar tickets",
    )
    p.add_argument("-s", "--status", action="append", choices=STATUSES)
    p.add_argument("--project")
    p.add_argument("-q", "--query")
    p.add_argument("--limit", type=int, default=300)
    p.add_argument(
        "--order",
        choices=("code", "priority"),
        default="code",
        help="Orden inicial: código (reciente) o prioridad",
    )
    p.add_argument(
        "--refresh",
        type=float,
        default=None,
        metavar="SEGUNDOS",
        help="Relee la base de datos cada N segundos (0 desactiva; por defecto, config.json)",
    )
    p.set_defaults(func=cmd_tui)

    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.db:
        os.environ["TAIKI_DB"] = args.db
    try:
        return args.func(args)
    except domain.TicketError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
