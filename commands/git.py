"""Comandos de integración con Git."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from ticketlib import audit as audit_lib
from ticketlib import repo
from ticketlib.githook import (
    BYPASS_TOKEN,
    check_commit_message,
    check_push,
    install_commit_msg_hook,
    install_post_commit_hook,
    install_pre_commit_hook,
    install_pre_push_hook,
)
from ticketlib.githook import git as _git

from .common import _conn


def cmd_link(args: argparse.Namespace) -> int:
    with _conn() as conn:
        ticket = repo.link_ticket(
            conn,
            args.code,
            args.kind,
            args.ref,
            actor="usuario",
            actor_type="usuario",
        )
    print(f"{args.kind} enlazado a {ticket['code']}: {args.ref}")
    return 0


def cmd_auto_link(args: argparse.Namespace) -> int:
    cwd = Path(args.repo).expanduser() if args.repo else Path.cwd()
    commit = args.commit or "HEAD"
    message = args.message
    if message is None:
        message = _git("show", "-s", "--format=%B", commit, cwd=cwd)
    full = _git("rev-parse", commit, cwd=cwd)

    with _conn() as conn:
        result = repo.auto_link_commit(conn, message, full)

    if not args.quiet:
        if result["linked"]:
            print(f"{full[:12]} enlazado a {', '.join(result['linked'])}")
        elif not result["missing"]:
            print("El mensaje no menciona tickets.")
    if result["missing"]:
        print(
            f"Sin ticket para: {', '.join(result['missing'])}",
            file=sys.stderr,
        )
    return 0


def cmd_hook_install(args: argparse.Namespace) -> int:
    repo = args.repo or "."
    post = install_post_commit_hook(repo)
    message = install_commit_msg_hook(repo)
    push = install_pre_push_hook(repo)
    index = install_pre_commit_hook(repo)
    print(f"Hook post-commit listo: {post}")
    print(f"Hook commit-msg listo: {message}")
    print(f"Hook pre-push listo: {push}")
    print(f"Hook pre-commit listo: {index}")
    return 0


def cmd_hook_check_msg(args: argparse.Namespace) -> int:
    path = Path(args.file).expanduser()
    try:
        message = path.read_text(encoding="utf-8")
    except OSError as exc:
        print(f"Error: no se pudo leer {path}: {exc}", file=sys.stderr)
        return 2

    with _conn() as conn:
        result = check_commit_message(conn, message)

    for warning in result.get("warnings", []):
        print(f"Aviso: {warning}", file=sys.stderr)
    if result["ok"]:
        return 0
    if result.get("message"):
        print(
            f"{result['message']}\n"
            f"Si el commit no corresponde a un ticket, escribe {BYPASS_TOKEN}.",
            file=sys.stderr,
        )
        return 1
    if result["missing"]:
        detail = f"Tickets inexistentes en el asunto: {', '.join(result['missing'])}."
    elif result.get("unconfirmed"):
        detail = (
            "Tickets no reclamados en el asunto: "
            + ", ".join(result["unconfirmed"])
            + ". Deben estar en_progreso, completado o confirmado; "
            "el push exige confirmación del usuario."
        )
    else:
        detail = "El asunto no cita ningún ticket."
    print(
        f"{detail}\n"
        "Añade el código T-XXXX del ticket en el asunto (los del cuerpo solo "
        f"se avisan) o escribe {BYPASS_TOKEN} si el commit no corresponde a un ticket.",
        file=sys.stderr,
    )
    return 1


def cmd_hook_check_push(args: argparse.Namespace) -> int:
    refs = sys.stdin.read()
    with _conn() as conn:
        result = check_push(conn, args.repo or ".", refs)
    if result["ok"]:
        return 0
    ticket_blocked = False
    for item in result["blocked"]:
        if item["unconfirmed"]:
            ticket_blocked = True
            detail = "sin confirmar: " + ", ".join(item["unconfirmed"])
        elif item["missing"]:
            ticket_blocked = True
            detail = "inexistentes: " + ", ".join(item["missing"])
        else:
            detail = item.get("message") or item["reason"] or "sin ticket"
        print(f"  {item['commit'][:12]}  {detail}", file=sys.stderr)
    if ticket_blocked:
        print(
            "Push bloqueado: confirma los tickets con `taiki confirm …` "
            "antes de publicar.",
            file=sys.stderr,
        )
    return 1


def cmd_audit(args: argparse.Namespace) -> int:
    with _conn() as conn:
        data = audit_lib.audit(
            conn,
            repo=args.repo or ".",
            days=args.days,
            limit=args.limit,
            stalled_days=args.stalled_days,
        )
    if args.json:
        print(json.dumps(data, ensure_ascii=False, indent=2))
        return 0

    print(f"Auditoría · {data['repo']} · últimos {data['days']} días")
    print()
    commits = data["commits"]
    print(f"Commits sin ticket ({len(commits)})")
    for item in commits:
        print(f"  {item['commit']}  {item['date']}  {item['subject']}")
    if not commits:
        print("  ninguno")
    print()
    stalled = data["stalled"]
    print(f"Tickets en progreso sin actividad ({len(stalled)})")
    for item in stalled:
        last = item["last_ts"] or "sin eventos"
        print(f"  {item['code']}  {item['title']}  ({last})")
    if not stalled:
        print("  ninguno")
    print()
    stale = data.get("stale_claims") or []
    print(f"Claims caducados ({len(stale)})")
    for item in stale:
        print(
            f"  {item['code']}  {item.get('owner_session') or '?'}  "
            f"lease {item.get('lease_until') or '-'}"
        )
    if stale:
        print("  Recupéralos con: taiki claim <código> --steal")
    else:
        print("  ninguno")
    print()
    unresolved = data["unresolved"]
    print(f"Cerrados sin resolución ({len(unresolved)})")
    for item in unresolved:
        print(f"  {item['code']}  [{item['status']}]  {item['title']}")
    if not unresolved:
        print("  ninguno")
    print()
    uncommitted = data["uncommitted"]
    epics = data.get("uncommitted_epics") or []
    print(f"Hojas cerradas sin commit enlazado ({len(uncommitted)})")
    for item in uncommitted:
        print(f"  {item['code']}  [{item['status']}]  {item['title']}")
    if not uncommitted:
        print("  ninguna")
    if epics:
        print(
            f"  Épicas excluidas ({len(epics)}): no tienen commit propio por "
            "diseño; su trabajo se reparte entre las hojas."
        )
    if uncommitted or epics:
        print(
            "  El audit solo detecta enlaces ausentes: «sin commit enlazado» "
            "no prueba que exista un «commit verificado»; revísalo con "
            "`taiki show <código>`."
        )
    print()
    misnumbered = data["misnumbered"]
    print(f"Tareas con código plano ({len(misnumbered)})")
    for item in misnumbered:
        print(f"  {item['code']} → {item['new_code']}  {item['title']}")
    if not misnumbered:
        print("  ninguna")
    print()
    gaps = data["gaps"]
    print(f"Huecos de numeración ({len(gaps)})")
    for item in gaps:
        print(f"  {item['code']}  {item['reason']}")
    if not gaps:
        print("  ninguno")
    print()
    hidden = data.get("hidden") or []
    print(f"Códigos ocultos que parecen huecos ({len(hidden)})")
    for item in hidden:
        print(f"  {item['code']}  [{item['status']}]  {item['reason']}")
    if not hidden:
        print("  ninguno")
    print()
    overlaps = data.get("overlaps") or []
    print(f"Solapamientos entre tickets activos ({len(overlaps)})")
    for item in overlaps:
        print(f"  {item['a']} ~ {item['b']}: {', '.join(item['patterns'])}")
    if not overlaps:
        print("  ninguno")
    print()
    dirty = data.get("dirty") or []
    print(f"Ficheros sin stagear o sin seguimiento ({len(dirty)})")
    for item in dirty[:10]:
        print(f"  {item}")
    if len(dirty) > 10:
        print(f"  … y {len(dirty) - 10} más")
    if not dirty:
        print("  ninguno")
    print()
    fixes = data.get("worktrees") or []
    print(f"Avisos de worktrees ({len(fixes)})")
    for item in fixes:
        print(f"  {item}")
    if not fixes:
        print("  ninguno")
    print()
    fleet = data["servers"]
    server_summary = fleet["summary"]
    print(
        f"Servidores MCP: {server_summary['total']} · "
        f"huérfanos {server_summary['orphans']} · "
        f"desactualizados {server_summary['stale']} · "
        f"duplicados {server_summary['duplicates']} · "
        f"sin registro {server_summary['unregistered']}"
    )
    for item in fleet["items"]:
        if item["state"] in ("huérfano", "desactualizado", "sin registro"):
            print(f"  {item['pid']}  {item['state']}  padre {item['ppid'] or '-'}")
    if server_summary["orphans"] or server_summary["dead"]:
        print("  Limpia con: taiki servers --prune --apply")
    if server_summary["stale"]:
        print(
            "  El plugin intenta recargarlo al quedar la sesión idle; "
            "si persiste, reinicia OpenCode."
        )
    print()
    hooks = data["hooks"]
    marks = " · ".join(
        f"{name} {'sí' if value else 'no'}" for name, value in hooks.items()
    )
    print(f"Hooks: {marks}")
    return 0
