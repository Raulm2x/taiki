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
    cmd_brief,
    cmd_claim,
    cmd_comment,
    cmd_commit,
    cmd_complete,
    cmd_demote,
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
    cmd_memory,
    cmd_move,
    cmd_new,
    cmd_next,
    cmd_open,
    cmd_scratch,
    cmd_overview,
    cmd_promote,
    cmd_renumber,
    cmd_repair_gaps,
    cmd_reject,
    cmd_release,
    cmd_remember,
    cmd_reopen,
    cmd_scheme_install,
    cmd_servers,
    cmd_scheme_uninstall,
    cmd_sys_audit,
    cmd_sys_export,
    cmd_sys_snapshot,
    cmd_sys_status,
    cmd_show,
    cmd_skills,
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
from commands.workflow import cmd_authorize, cmd_review
from ticketlib import domain
from ticketlib.domain import (
    ISOLATION_MODES,
    KINDS,
    LINK_KINDS,
    PRIORITIES,
    STATUSES,
)
from ticketlib.memory import MEMORY_KINDS, MEMORY_SCOPES, MEMORY_STATUSES


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
    p.add_argument(
        "--ready",
        action="store_true",
        help="Solo tareas sin dependencias abiertas",
    )
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

    p = sub.add_parser(
        "attach",
        help="Agrupa tickets bajo una épica (equivale a move --keep-code)",
        description="Cuelga los tickets de una épica conservando su código; "
        "equivale a 'move --keep-code'.",
    )
    p.add_argument("parent")
    p.add_argument("codes", nargs="+")
    p.add_argument("--agent", default="usuario")
    p.set_defaults(func=cmd_attach)

    p = sub.add_parser("detach", help="Saca tickets de su épica")
    p.add_argument("codes", nargs="+")
    p.add_argument("--agent", default="usuario")
    p.set_defaults(func=cmd_detach)

    p = sub.add_parser(
        "move",
        help="Mueve tickets a otra épica (dry-run por defecto)",
        description="Reasigna los tickets a la épica --parent. Sin --renumber "
        "conserva el código (como attach); simula salvo con --apply.",
    )
    p.add_argument("codes", nargs="+")
    p.add_argument("--parent", required=True, help="Épica destino")
    modo = p.add_mutually_exclusive_group()
    modo.add_argument(
        "--renumber",
        action="store_true",
        help="Renombra los códigos a <padre>.NN",
    )
    modo.add_argument(
        "--keep-code",
        action="store_true",
        help="Conserva el código plano (defecto; como attach)",
    )
    p.add_argument("--apply", action="store_true", help="Aplica el plan")
    p.add_argument("--json", action="store_true", help="Emite JSON")
    p.set_defaults(func=cmd_move)

    p = sub.add_parser(
        "promote",
        help="Convierte una tarea en épica (dry-run por defecto)",
        description="Desvincula el ticket de su padre para que pueda tener "
        "tareas; simula salvo con --apply.",
    )
    p.add_argument("code")
    p.add_argument("--apply", action="store_true", help="Aplica el plan")
    p.add_argument("--json", action="store_true", help="Emite JSON")
    p.set_defaults(func=cmd_promote)

    p = sub.add_parser(
        "demote",
        help="Convierte una épica en tarea (dry-run por defecto)",
        description="Reparte sus tareas en --children-to (o root) y mueve la "
        "épica a --parent si se indica; simula salvo con --apply.",
    )
    p.add_argument("code")
    p.add_argument(
        "--children-to",
        required=True,
        help="Épica destino de las tareas, o 'root' para dejarlas en la raíz",
    )
    p.add_argument("--parent", help="Épica destino de la épica degradada")
    p.add_argument(
        "--renumber",
        action="store_true",
        help="Renumera como <padre>.NN los tickets que ganan padre",
    )
    p.add_argument("--apply", action="store_true", help="Aplica el plan")
    p.add_argument("--json", action="store_true", help="Emite JSON")
    p.set_defaults(func=cmd_demote)

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
    p.add_argument(
        "--isolation",
        choices=ISOLATION_MODES,
        help="Aislamiento: none, scope (paths+resources) o worktree (defecto)",
    )
    p.add_argument(
        "--resource",
        action="append",
        dest="resources",
        help="Recurso tipado (systemd:user/<unit>, package:<name>…); repetible",
    )
    p.add_argument(
        "--reversal",
        help='Reversión JSON: {"kind": "...", "ref": "...", "command": "..."}',
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
        "(procesos antiguos) y los renombra. Solo toca los adoptados con "
        "attach si se pasa --adopted; sin códigos usa todos los candidatos. "
        "Simula salvo con --apply.",
    )
    p.add_argument(
        "codes", nargs="*", help="Tareas concretas (defecto: todas)"
    )
    p.add_argument(
        "--adopted",
        action="store_true",
        help="Incluye los tickets adoptados con attach",
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
    p.add_argument(
        "--isolation",
        choices=ISOLATION_MODES,
        help="Aislamiento: none, scope (paths+resources) o worktree",
    )
    p.add_argument(
        "--resource",
        action="append",
        dest="resources",
        help="Recurso tipado; repetible (reemplaza el conjunto completo)",
    )
    p.add_argument(
        "--reversal",
        help='Reversión JSON: {"kind": "...", "ref": "...", "command": "..."}',
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
        "authorize",
        help="Autoriza comandos de alto riesgo para un ticket (solo usuario)",
        description="Registra la autorización explícita del usuario para que "
        "el guard acepte comandos de alto riesgo (sudo, systemctl de sistema, "
        "gestores de paquetes, escrituras en /etc, /usr, /boot o /var/lib). "
        "No cambia el estado del ticket. Solo el usuario puede autorizar: los "
        "agentes deben pedirlo.",
    )
    p.add_argument("code")
    p.add_argument(
        "--why", required=True, help="Motivo de la autorización"
    )
    p.set_defaults(func=cmd_authorize)

    p = sub.add_parser(
        "review",
        help="Deja constancia de la revisión de un ticket sensible (solo usuario)",
        description="Registra que el usuario revisó un ticket que toca rutas "
        "sensibles. Es opcional y ya no bloquea el paso a 'verificado': "
        "`taiki confirm` es la revisión especial. Solo el usuario puede "
        "revisar.",
    )
    p.add_argument("code")
    p.add_argument(
        "--why", required=True, help="Motivo o alcance de la revisión"
    )
    p.set_defaults(func=cmd_review)

    p = sub.add_parser(
        "confirm",
        help="Confirma uno o varios tickets completados o verificados",
        description="Confirma varios tickets de forma atómica: si alguno no "
        "está en 'completado' o 'verificado' no se confirma ninguno.",
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
        help="Rechaza un ticket completado/verificado y lo devuelve a en_progreso",
    )
    p.add_argument("code")
    p.add_argument("--why", required=True, help="Motivo del rechazo")
    p.add_argument(
        "--revert",
        action="store_true",
        help="Revierte el cambio live y cancela el ticket (solo usuario)",
    )
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

    p = sub.add_parser("memory", help="Gestiona la memoria semántica")
    memory_sub = p.add_subparsers(dest="memory_action", required=True)
    p.set_defaults(func=cmd_memory)

    m = memory_sub.add_parser("add", help="Añade una memoria")
    m.add_argument("--scope", choices=MEMORY_SCOPES, default="project")
    m.add_argument("--kind", choices=MEMORY_KINDS, default="fact")
    m.add_argument("--key")
    m.add_argument("--title", required=True)
    m.add_argument("--content", "--body", dest="content", required=True)
    m.add_argument("--evidence", action="append", default=[])
    m.add_argument("--confidence", type=float, default=0.5)
    m.add_argument(
        "--ticket-kinds",
        nargs="+",
        choices=KINDS,
        help="Tipos de ticket aplicables; sin la opción, la memoria es general",
    )
    m.add_argument(
        "--tags",
        nargs="*",
        help="Etiquetas de dominio en minúsculas (slug corto)",
    )
    m.add_argument("--pin", action="store_true")
    m.add_argument("--json", action="store_true")
    m.set_defaults(func=cmd_memory)

    m = memory_sub.add_parser("list", help="Lista memorias")
    m.add_argument("--scope", choices=MEMORY_SCOPES)
    m.add_argument("--kind", choices=MEMORY_KINDS)
    m.add_argument(
        "--ticket-kinds",
        nargs="+",
        choices=KINDS,
        help="Solo memorias aplicables a alguno de esos tipos de ticket",
    )
    m.add_argument("--status", choices=MEMORY_STATUSES)
    m.add_argument("--limit", type=int, default=50)
    m.add_argument("--json", action="store_true")
    m.set_defaults(func=cmd_memory)

    m = memory_sub.add_parser("show", help="Detalle de una memoria")
    m.add_argument("code")
    m.add_argument("--json", action="store_true")
    m.set_defaults(func=cmd_memory)

    m = memory_sub.add_parser("search", help="Busca memorias por texto")
    m.add_argument("query", help="Texto a buscar en título y contenido")
    m.add_argument("--scope", choices=MEMORY_SCOPES)
    m.add_argument("--kind", choices=MEMORY_KINDS)
    m.add_argument("--limit", type=int, default=10)
    m.add_argument("--json", action="store_true")
    m.set_defaults(func=cmd_memory)

    m = memory_sub.add_parser("pending", help="Propuestas pendientes")
    m.add_argument("--limit", type=int, default=50)
    m.add_argument(
        "--diff",
        action="store_true",
        help="Resume el cambio frente a la memoria sustituida",
    )
    m.add_argument("--json", action="store_true")
    m.set_defaults(func=cmd_memory)

    m = memory_sub.add_parser(
        "candidates", help="Candidatos de aprendizaje del histórico"
    )
    m.add_argument("--since", help="Ventana temporal: 90d o una fecha ISO")
    m.add_argument(
        "--kind", choices=MEMORY_KINDS, help="Filtra por tipo de memoria sugerida"
    )
    m.add_argument("--limit", type=int, default=None)
    m.add_argument(
        "--all", action="store_true", help="Ignora candidate_max y lista todo"
    )
    m.add_argument("--json", action="store_true")
    m.set_defaults(func=cmd_memory)

    m = memory_sub.add_parser(
        "review", help="Paquete de revisión para el curator"
    )
    m.add_argument(
        "--write",
        action="store_true",
        help="Guarda el paquete en .taiki/generated/memory-review.json",
    )
    m.add_argument(
        "--run-command",
        action="store_true",
        help="Ejecuta memory.review_command y deja sus salidas como propuestas",
    )
    m.add_argument(
        "--dry-run",
        action="store_true",
        help="Con --run-command, valida sin crear propuestas",
    )
    m.add_argument("--json", action="store_true")
    m.set_defaults(func=cmd_memory)

    m = memory_sub.add_parser(
        "curate", help="Caduca, archiva y deduplica memorias de forma determinista"
    )
    m.add_argument(
        "--dry-run",
        action="store_true",
        help="Muestra el plan sin escribir estados ni ledger",
    )
    m.add_argument("--json", action="store_true")
    m.set_defaults(func=cmd_memory)

    m = memory_sub.add_parser("stats", help="Estadísticas de la memoria")
    m.add_argument("--json", action="store_true")
    m.set_defaults(func=cmd_memory)

    m = memory_sub.add_parser(
        "impact", help="Tendencias de cierre, intentos y lead time"
    )
    m.add_argument("--days", type=int, default=30)
    m.add_argument("--json", action="store_true")
    m.set_defaults(func=cmd_memory)

    m = memory_sub.add_parser(
        "export", help="Exporta un snapshot JSON o Markdown de la memoria"
    )
    m.add_argument("path")
    m.add_argument(
        "--format", choices=["json", "markdown"], help="Formato de salida"
    )
    m.add_argument("--json", action="store_true")
    m.set_defaults(func=cmd_memory)

    m = memory_sub.add_parser(
        "import", help="Importa un snapshot con estrategia keep/replace/new"
    )
    m.add_argument("path")
    m.add_argument("--strategy", choices=["keep", "replace", "new"], default="keep")
    m.add_argument("--dry-run", action="store_true")
    m.add_argument("--json", action="store_true")
    m.set_defaults(func=cmd_memory)

    m = memory_sub.add_parser(
        "rollback", help="Revierte por asiento de ledger o por snapshot"
    )
    m.add_argument("ledger_id", nargs="?", type=int)
    m.add_argument("--snapshot", help="Snapshot de memoria a restaurar")
    m.add_argument("--dry-run", action="store_true")
    m.add_argument("--why")
    m.add_argument("--json", action="store_true")
    m.set_defaults(func=cmd_memory)

    m = memory_sub.add_parser("approve", help="Aprueba una propuesta")
    m.add_argument("code")
    m.add_argument("--actor", default="usuario")
    m.add_argument("--why")
    m.add_argument("--json", action="store_true")
    m.set_defaults(func=cmd_memory)

    m = memory_sub.add_parser("reject", help="Rechaza una propuesta")
    m.add_argument("code")
    m.add_argument("--actor", default="usuario")
    m.add_argument("--why")
    m.add_argument("--json", action="store_true")
    m.set_defaults(func=cmd_memory)

    m = memory_sub.add_parser("pin", help="Fija una memoria en el brief")
    m.add_argument("code")
    m.add_argument("--json", action="store_true")
    m.set_defaults(func=cmd_memory)

    m = memory_sub.add_parser("unpin", help="Desfija una memoria")
    m.add_argument("code")
    m.add_argument("--json", action="store_true")
    m.set_defaults(func=cmd_memory)

    m = memory_sub.add_parser("rm", help="Archiva una memoria (nunca la borra)")
    m.add_argument("code")
    m.add_argument("--why", required=True)
    m.add_argument("--json", action="store_true")
    m.set_defaults(func=cmd_memory)

    m = memory_sub.add_parser("edit", help="Edita una memoria")
    m.add_argument("code")
    m.add_argument("-t", "--title")
    m.add_argument("--content", "--body", dest="content")
    m.add_argument("--description")
    m.add_argument("--key")
    m.add_argument("--kind", choices=MEMORY_KINDS)
    m.add_argument("--confidence", type=float)
    m.add_argument("--paths", nargs="+")
    m.add_argument(
        "--tags",
        nargs="*",
        help="Reemplaza las etiquetas de dominio de la memoria",
    )
    m.add_argument(
        "--ticket-kinds",
        nargs="+",
        choices=KINDS,
        help="Reemplaza las etiquetas de tipo de ticket de la memoria",
    )
    m.add_argument("--why")
    m.add_argument("--json", action="store_true")
    m.set_defaults(func=cmd_memory)

    p = memory_sub.add_parser(
        "playbook", help="Playbooks: procedimientos exportables como skills"
    )
    playbook_sub = p.add_subparsers(dest="playbook_action", required=True)
    p.set_defaults(func=cmd_memory)

    m = playbook_sub.add_parser("add", help="Crea un playbook")
    m.add_argument("--slug", required=True)
    m.add_argument("--title", required=True)
    m.add_argument("--description", required=True)
    source = m.add_mutually_exclusive_group(required=True)
    source.add_argument("--content", "--body", dest="content")
    source.add_argument("--file", help="Lee el cuerpo de un fichero")
    m.add_argument(
        "--support",
        action="append",
        default=[],
        metavar="RUTA",
        help="Fichero de apoyo; se clasifica por extensión",
    )
    m.add_argument("--evidence", action="append", default=[])
    m.add_argument("--confidence", type=float, default=0.5)
    m.add_argument("--json", action="store_true")
    m.set_defaults(func=cmd_memory)

    m = playbook_sub.add_parser("list", help="Lista playbooks")
    m.add_argument("--status", choices=MEMORY_STATUSES)
    m.add_argument(
        "--all", action="store_true", help="Incluye archivados y sustituidos"
    )
    m.add_argument("--limit", type=int, default=50)
    m.add_argument("--json", action="store_true")
    m.set_defaults(func=cmd_memory)

    m = playbook_sub.add_parser("show", help="Detalle de un playbook")
    m.add_argument("ref", help="Slug o código M-####")
    m.add_argument("--json", action="store_true")
    m.set_defaults(func=cmd_memory)

    m = playbook_sub.add_parser(
        "export", help="Exporta skills a .taiki/generated/skills"
    )
    m.add_argument("ref", nargs="?", help="Slug o código M-####")
    m.add_argument("--all", action="store_true", help="Exporta todos los activos")
    m.add_argument("--json", action="store_true")
    m.set_defaults(func=cmd_memory)

    m = playbook_sub.add_parser(
        "archive", help="Archiva un playbook (nunca lo borra)"
    )
    m.add_argument("ref", help="Slug o código M-####")
    m.add_argument("--why", required=True)
    m.add_argument("--json", action="store_true")
    m.set_defaults(func=cmd_memory)

    p = sub.add_parser(
        "skills", help="Skills curadas de AutoSkills para el stack del proyecto"
    )
    skills_sub = p.add_subparsers(dest="skills_action", required=True)
    p.set_defaults(func=cmd_skills)

    s = skills_sub.add_parser(
        "suggest", help="Sugiere skills curadas para el stack detectado"
    )
    s.add_argument("--limit", type=int, default=20)
    s.add_argument("--json", action="store_true")
    s.set_defaults(func=cmd_skills)

    s = skills_sub.add_parser(
        "install", help="Instala una skill como playbook propuesto (solo usuario)"
    )
    s.add_argument("name", help="Nombre de la skill en el registro")
    s.add_argument("--yes", action="store_true", help="Confirma sin preguntar")
    s.add_argument("--json", action="store_true")
    s.set_defaults(func=cmd_skills)

    s = skills_sub.add_parser("list", help="Lista las skills instaladas")
    s.add_argument("--json", action="store_true")
    s.set_defaults(func=cmd_skills)

    s = skills_sub.add_parser(
        "remove", help="Archiva una skill instalada (nunca la borra)"
    )
    s.add_argument("name")
    s.add_argument("--why")
    s.set_defaults(func=cmd_skills)

    s = skills_sub.add_parser(
        "sync", help="Exporta los playbooks activos a .taiki/generated/skills"
    )
    s.add_argument("--json", action="store_true")
    s.set_defaults(func=cmd_skills)

    p = sub.add_parser(
        "remember", help="Atajo: guarda una preferencia activa del usuario"
    )
    p.add_argument("text", nargs="+")
    p.add_argument("-t", "--title")
    p.add_argument("--ticket", help="Ticket de evidencia (si no, la sesión)")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_remember)

    p = sub.add_parser("brief", help="Brief operativo de la memoria")
    p.add_argument("--write", action="store_true", help="Escribe el artefacto")
    p.add_argument("--json", action="store_true")
    p.add_argument(
        "--section", choices=("project", "user", "all"), default="all"
    )
    p.add_argument("--budget", type=int, help="Presupuesto en caracteres")
    p.set_defaults(func=cmd_brief)

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
    mcp_add.add_argument(
        "--force-agents",
        action="store_true",
        help="sobrescribe los perfiles de .opencode/agents con la versión de Taiki",
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

    p = sub.add_parser(
        "release",
        help="Publica la integración en main: squash, tag y CHANGELOG",
        description="Aplana la rama de integración (develop por defecto) sobre "
        "main en un único commit verificado y etiquetado. Solo lo ejecuta el "
        "usuario: los perfiles de agente deniegan `taiki *`.",
    )
    p.add_argument("--repo", help="Repositorio (por defecto, el proyecto actual)")
    p.add_argument(
        "--integration",
        help="Rama de integración (por defecto orchestrator.integration_branch)",
    )
    p.add_argument(
        "--bump",
        choices=("major", "minor", "patch"),
        help="Fuerza el incremento de versión",
    )
    p.add_argument("--dry-run", action="store_true", help="Solo muestra el release")
    p.add_argument(
        "--push",
        action="store_true",
        help="Publica en el remoto en el mismo comando: integración (si va por "
        "delante), main, tag y reconciliación. Incompatible con --dry-run",
    )
    p.add_argument(
        "--no-verify",
        action="store_true",
        help="No ejecuta los gates (solo si ya se verificaron)",
    )
    p.add_argument(
        "--allow-ahead",
        action="store_true",
        help="Permite liberar con la integración por delante de su upstream "
        "(sin push): el release queda solo en local y hay que subir la rama "
        "después. Nunca permite ir por detrás ni una divergencia",
    )
    p.add_argument(
        "--verify-cmd",
        help="Comando de gates (por defecto tests/run.py --verify; "
        "también TAIKI_RELEASE_VERIFY)",
    )
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_release)

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
        "merge",
        help="rebase, tests y merge ff-only del worktree",
        description="Integra el worktree en la rama de integración (develop "
        "por defecto). Exige que el ticket esté 'confirmado'; usa --force "
        "para el bypass consciente.",
    )
    wt_merge.add_argument("code", nargs="?")
    wt_merge.add_argument("--path", help="ruta concreta del worktree")
    wt_merge.add_argument("--no-tests", action="store_true")
    wt_merge.add_argument(
        "--delete", action="store_true", help="retira el worktree tras mergear"
    )
    wt_merge.add_argument(
        "--force",
        action="store_true",
        help="Mergea aunque el ticket no esté confirmado",
    )
    wt_merge.add_argument("--json", action="store_true")
    wt_merge.set_defaults(func=cmd_worktree_merge)

    p = sub.add_parser(
        "sys",
        help="Workspace de sistema: estado, raíces y dotfiles",
        description="Trabaja sobre el workspace de sistema (repo Git propio en "
        "~/.local/share/taiki-sys): estado del repo, raíces del modo scope y "
        "detección de dotfiles.",
    )
    sys_sub = p.add_subparsers(dest="sys_action", required=True)
    sys_status = sys_sub.add_parser("status", help="estado del workspace de sistema")
    sys_status.add_argument("--json", action="store_true")
    sys_status.set_defaults(func=cmd_sys_status)
    sys_snap = sys_sub.add_parser(
        "snapshot",
        help="guarda un fichero vivo o la lista de paquetes en el repo de sistema",
        description="Copia el fichero a snapshots/<T-XXXX>/<timestamp>/ en el "
        "repo de sistema, lo commitea y anota la reversión en el ticket. Si el "
        "fichero vive en el repo de dotfiles no se copia: se commitea allí. "
        "Los paquetes se guardan con pacman -Qqe y el rollback es manual.",
    )
    sys_snap.add_argument("code")
    sys_snap_group = sys_snap.add_mutually_exclusive_group(required=True)
    sys_snap_group.add_argument("--path", help="fichero absoluto a copiar")
    sys_snap_group.add_argument(
        "--packages",
        action="store_true",
        help="guarda la lista pacman -Qqe (rollback manual)",
    )
    sys_snap.add_argument(
        "--from",
        dest="source",
        metavar="FILE",
        help="lee la lista de paquetes de FILE en vez de ejecutar pacman",
    )
    sys_snap.add_argument("--json", action="store_true")
    sys_snap.set_defaults(func=cmd_sys_snapshot)
    sys_export = sys_sub.add_parser(
        "export",
        help="exporta tickets y manifiesto a records/ en el repo de sistema",
        description="Escribe records/tickets.json, records/tickets.md y "
        "records/manifest.json (snapshots, commits/external y hashes) y lo "
        "commitea. La DB no se versiona.",
    )
    sys_export.add_argument("code", nargs="?", help="ticket que firma el commit")
    sys_export.add_argument("--json", action="store_true")
    sys_export.set_defaults(func=cmd_sys_export)
    sys_audit = sys_sub.add_parser(
        "audit",
        help="compara los hashes del manifiesto con los ficheros actuales",
        description="Verifica los paths registrados en records/manifest.json y "
        "marca los cambios cubiertos por un ticket abierto. No escanea "
        "ficheros nuevos (follow-up).",
    )
    sys_audit.add_argument("--json", action="store_true")
    sys_audit.set_defaults(func=cmd_sys_audit)

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
    p.add_argument(
        "-m",
        "--message",
        action="append",
        nargs="?",
        default=None,
        help="Mensaje del commit; varios -m se concatenan como párrafos",
    )
    p.add_argument("-t", "--ticket", help="Código del ticket (o TAIKI_TICKET)")
    p.add_argument(
        "--repo",
        help="Repositorio donde commitear (por ejemplo, dotfiles); mantiene la "
        "DB del workspace y enlaza el hash como external repo@sha",
    )
    p.add_argument("--agent", help="Agente que hizo el commit (atribución)")
    p.add_argument("--session", help="Sesión del agente que hizo el commit")
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
