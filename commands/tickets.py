"""Comandos de consulta y creación de tickets."""

from __future__ import annotations

import argparse
import os
import sys
from dataclasses import replace
from pathlib import Path

from ticketlib import db, render, repo, scratch, sessions, workspace
from ticketlib.domain import OPEN_STATUSES, TicketError
from ticketlib.util import resolve_scope

from .common import _clean_expired, _conn, _read_body, _show


def _annotate(
    conn, tickets: list[dict]
) -> dict[str, str | None]:
    """Completa `last_actor` y devuelve el mapa de sesiones vivas."""
    activity = sessions.ticket_activity(conn, [ticket["code"] for ticket in tickets])
    sessions.decorate(tickets, activity)
    return {code: info["session"] for code, info in activity.items()}


def _tree_rows(tickets: list[dict]) -> list[dict]:
    """Ordena raíces y tareas con su prefijo de árbol."""
    by_id = {ticket["id"]: ticket for ticket in tickets}
    children: dict[str, list[dict]] = {}
    roots: list[dict] = []
    for ticket in tickets:
        parent = ticket.get("parent_id")
        if parent is not None and parent in by_id:
            children.setdefault(by_id[parent]["code"], []).append(ticket)
        else:
            roots.append(ticket)
    rows: list[dict] = []
    for root in roots:
        kids = sorted(children.get(root["code"], []), key=lambda t: t["id"])
        root["tree_prefix"] = "▾ " if kids else ""
        rows.append(root)
        for child in kids:
            child["tree_prefix"] = "  "
            if "." in child["code"]:
                child["display_code"] = "." + child["code"].rsplit(".", 1)[1]
            rows.append(child)
    return rows


SUMMARY_JSON_FIELDS = (
    "code",
    "title",
    "status",
    "priority",
    "kind",
    "project",
    "assigned_to",
    "parent_id",
    "updated_at",
)


def _json_summary(ticket: dict) -> dict:
    data = {
        key: ticket[key]
        for key in SUMMARY_JSON_FIELDS
        if ticket.get(key) is not None
    }
    progress = ticket.get("progress") or {}
    if progress.get("total"):
        data["progress"] = progress
    for key in ("paths", "tags"):
        if ticket.get(key):
            data[key] = ticket[key]
    return data


def cmd_list(args: argparse.Namespace) -> int:
    statuses = args.status or (None if args.all else list(OPEN_STATUSES))
    base_filter = repo.TicketFilter(
        statuses=statuses,
        kind=args.kind,
        priority=args.priority,
        project=args.project,
        assignee=args.assignee,
        query=args.query,
        include_archived=args.archived,
        tag=args.tag,
        include_scratch=getattr(args, "include_scratch", False),
    )
    if args.parent:
        ticket_filter = replace(base_filter, parent_id=args.parent)
    elif args.flat or args.tree:
        ticket_filter = base_filter
    else:
        ticket_filter = replace(base_filter, roots_only=True)
    limit, offset = args.limit, args.offset
    if args.tree:
        limit, offset = None, 0
    summary = bool(getattr(args, "json_summary", False))

    with _conn() as conn:
        _clean_expired(conn)
        tickets = repo.list_tickets(
            conn,
            filters=ticket_filter,
            limit=limit,
            offset=offset,
            order=args.sort,
            detail=bool((args.json or args.compact) and not summary),
        )
        total = repo.count_tickets(conn, filters=ticket_filter)
        hidden = 0
        # El resumen lean no incluye hidden_children: evita un segundo COUNT
        # sobre 20k filas en la ruta caliente de los agentes.
        if ticket_filter.roots_only and not summary:
            hidden = max(
                0,
                repo.count_tickets(conn, filters=base_filter) - total,
            )
        if args.tree:
            tickets = _tree_rows(tickets)
        # El resumen lean no necesita sesiones ni actores: evita la consulta.
        session_map = {} if summary else _annotate(conn, tickets)

    if args.json or args.compact or summary:
        payload = []
        for ticket in tickets:
            if summary:
                payload.append(_json_summary(ticket))
                continue
            item = {
                key: value
                for key, value in ticket.items()
                if key not in ("tree_prefix", "display_code")
            }
            item["session_id"] = session_map.get(ticket["code"])
            payload.append(item)
        scope = (
            "tree"
            if args.tree
            else "parent"
            if args.parent
            else "flat"
            if args.flat
            else "roots"
        )
        envelope = {
            "count": len(tickets),
            "total": total,
            "offset": offset,
            "limit": limit,
            "order": args.sort,
            "scope": scope,
            "tickets": payload,
        }
        if not summary:
            envelope["hidden_children"] = hidden
        print(
            render.to_json(
                envelope,
                compact=getattr(args, "compact", False),
            )
        )
        return 0

    if not tickets:
        if total and args.offset:
            print(f"No hay más tickets (total: {total}).")
        else:
            print("No hay tickets.")
        return 0

    render.print_table(tickets, session_map=session_map)
    end = offset + len(tickets)
    if total > end or offset:
        print(
            f"\nMostrando {offset + 1}-{end} de {total} · "
            f"orden: {args.sort} · siguiente: --offset {end}"
        )
    if hidden:
        print(
            f"\n{hidden} subtarea(s) oculta(s) · --flat para verlas "
            "o --tree para el árbol"
        )
    return 0


def cmd_show(args: argparse.Namespace) -> int:
    with _conn() as conn:
        _clean_expired(conn)
        ticket = repo.get_ticket(conn, args.code)
        session_id = _annotate(conn, [ticket]).get(ticket["code"])
    _show(
        ticket,
        args.json or args.compact,
        session_id,
        compact=getattr(args, "compact", False),
    )
    return 0


def cmd_new(args: argparse.Namespace) -> int:
    title = args.title
    if not title:
        if not sys.stdin.isatty():
            print("Error: --title es obligatorio en modo no interactivo", file=sys.stderr)
            return 2
        title = input("Título: ").strip()
    if not title:
        print("Error: el título es obligatorio", file=sys.stderr)
        return 2

    body = _read_body(args.body, "Descripción (opcional): ")
    project, path = resolve_scope(args.project, args.path)
    config = workspace.load_config()
    defaults = config.get("defaults") or {}
    kind = args.kind or defaults.get("kind") or "otro"
    priority = args.priority or defaults.get("priority") or "media"

    with _conn() as conn:
        ticket = repo.create_ticket(
            conn,
            title=title,
            body=body,
            kind=kind,
            priority=priority,
            project=project,
            path=path,
            tags=args.tags,
            paths=getattr(args, "paths", None),
            parent_id=args.parent,
            created_by="usuario",
            actor_type="usuario",
        )
    print(f"Creado {ticket['code']}: {ticket['title']}")
    return 0


def cmd_repair_gaps(args: argparse.Namespace) -> int:
    with _conn() as conn:
        plan = repo.fill_gaps(
            conn,
            apply=args.apply,
            actor=args.agent or "usuario",
            actor_type="agente" if args.agent else "usuario",
        )
    if args.json:
        print(render.to_json(plan))
        return 0
    if not plan:
        print("No hay huecos de numeración.")
        return 0
    for item in plan:
        print(f"{item['code']}  {item['reason']}")
    if not args.apply:
        print("(simulación; usa --apply para cubrir los huecos)")
    else:
        print(f"Cubiertos {len(plan)} hueco(s) con tickets cancelados.")
    return 0


def cmd_renumber(args: argparse.Namespace) -> int:
    with _conn() as conn:
        plan = repo.renumber_children(
            conn,
            apply=args.apply,
            actor=args.agent or "usuario",
            actor_type="agente" if args.agent else "usuario",
        )
    if args.json:
        print(render.to_json(plan))
        return 0
    if not plan:
        print("No hay tareas con código fuera de patrón.")
        return 0
    for item in plan:
        print(f"{item['code']} → {item['new_code']}  {item['title']}")
    if not args.apply:
        print("(simulación; usa --apply para renumerar)")
    return 0


def cmd_update(args: argparse.Namespace) -> int:
    fields = {
        key: getattr(args, key)
        for key in (
            "title",
            "body",
            "kind",
            "priority",
            "project",
            "path",
            "paths",
            "tags",
        )
        if getattr(args, key) is not None
    }
    if not fields:
        print("Error: no se indicó ningún campo", file=sys.stderr)
        return 2
    with _conn() as conn:
        ticket = repo.update_ticket(
            conn, args.code, actor="usuario", actor_type="usuario", **fields
        )
    print(f"Actualizado {ticket['code']}")
    return 0


def cmd_claim(args: argparse.Namespace) -> int:
    with _conn() as conn:
        ticket = repo.claim_ticket(
            conn,
            args.code,
            agent=args.agent,
            actor_type="usuario",
            force=getattr(args, "force", False),
            steal=getattr(args, "steal", False),
        )
        created = None
        if getattr(args, "worktree", False):
            from ticketlib import worktree as worktree_lib

            try:
                created = worktree_lib.create(
                    workspace.project_root(),
                    ticket["code"],
                    title=ticket["title"],
                )
            except TicketError as exc:
                print(f"Error al crear el worktree: {exc}", file=sys.stderr)
                return 1
            repo.link_ticket(
                conn, ticket["code"], "worktree", str(created["path"])
            )
            repo.link_ticket(conn, ticket["code"], "rama", created["branch"])
    print(f"{ticket['code']} en progreso, asignado a {ticket['assigned_to']}")
    if created:
        print(f"Worktree: {created['path']} (rama {created['branch']})")
    return 0


def cmd_next(args: argparse.Namespace) -> int:
    with _conn() as conn:
        _clean_expired(conn)
        ticket = repo.next_ticket(conn, project=args.project)
        session_id = _annotate(conn, [ticket]).get(ticket["code"]) if ticket else None
    if ticket is None:
        print("No hay tickets pendientes.")
    else:
        _show(ticket, args.json, session_id)
    return 0


def cmd_scratch(args: argparse.Namespace) -> int:
    """Crea o gestiona tickets temporales (scratch) fuera de la tabla."""
    session_id = (
        os.environ.get("OPENCODE_SESSION_ID")
        or os.environ.get("AGY_SESSION_ID")
        or None
    )
    with _conn() as conn:
        if args.clean:
            codes = scratch.clean(conn)
            if codes:
                print(
                    f"Archivados {len(codes)} scratch caducados: "
                    + ", ".join(codes)
                )
            else:
                print("No había scratch caducados.")
            return 0
        if args.list:
            rows = scratch.list_scratch(conn)
            if not rows:
                print("Sin tickets scratch.")
                return 0
            print("Scratch (ocultos de la tabla):")
            for row in rows:
                limit = scratch.expires_at(conn, row["code"]) or "-"
                print(
                    f"{row['code']}  {row['status']:<12} caduca {limit:<19} "
                    f"{row['title']}"
                )
            return 0
        if args.drop:
            ticket = scratch.drop(conn, args.drop, session_id=session_id)
            print(f"{ticket['code']} archivado (scratch).")
            return 0
        if args.promote:
            ticket = scratch.promote(conn, args.promote, session_id=session_id)
            print(
                f"{ticket['code']} visible otra vez en la tabla "
                "(código T- propio)."
            )
            return 0

        ticket = scratch.create_or_reuse(
            conn,
            agent=args.agent,
            session_id=session_id,
            title=args.title,
            ttl=args.ttl,
        )
        limit = scratch.expires_at(conn, ticket["code"]) or "-"
        print(
            f"{ticket['code']}  scratch ({ticket['status']}, caduca {limit})"
        )
        print(
            "Oculto de `taiki list`, `stats`, `next` y la TUI; el código S- "
            "no consume números T-XXXX."
        )
        print(
            f"Promuévelo con `taiki scratch --promote {ticket['code']}` "
            "cuando el trabajo sea real."
        )
    return 0


def cmd_stats(args: argparse.Namespace) -> int:
    with _conn() as conn:
        _clean_expired(conn)
        data = repo.stats(conn)
    print(render.to_json(data) if args.json else render.render_stats(data))
    return 0


def cmd_overview(args: argparse.Namespace) -> int:
    with _conn() as conn:
        data = repo.stats(conn)
        rows = repo.list_tickets(conn, limit=getattr(args, "limit", 10))
        session_map = _annotate(conn, rows)
    print(f"Base de datos: {db.db_path()}")
    print(f"Proyecto: {workspace.label()}")
    print(f"Tickets: {data['total']} (abiertos: {data['abiertos']})")
    if rows:
        print()
        render.print_table(rows, session_map=session_map)
    return 0


def cmd_export(args: argparse.Namespace) -> int:
    with _conn() as conn:
        data = repo.export_all(conn)
    text = render.to_json(data) + "\n"
    if args.output:
        Path(args.output).expanduser().write_text(text, encoding="utf-8")
        print(f"Exportados {len(data['tickets'])} tickets a {args.output}")
    else:
        sys.stdout.write(text)
    return 0


def cmd_init(args: argparse.Namespace) -> int:
    adopted = getattr(args, "adopt", None)
    if getattr(args, "workspace", False) or adopted:
        info = workspace.init_workspace(
            getattr(args, "root", None),
            adopt=adopted,
            name=getattr(args, "name", None),
            force=bool(getattr(args, "force", False)),
        )
        print(f"Workspace: {info['workspace']}")
        print(f"Base de datos: {info['db']}")
        if info["adopted"]:
            print(f"Adoptada desde: {adopted}")
        print(f"Configuración: {info['config']}")
        if getattr(args, "mcp", False):
            from .mcp import cmd_mcp_install

            cmd_mcp_install(argparse.Namespace(antigravity=False))
        return 0

    info = workspace.resolve()
    if info["db"] is None:
        print(workspace.MISSING, file=sys.stderr)
        return 1
    with _conn() as conn:
        data = repo.stats(conn)
    print(f"Base de datos: {db.db_path()}")
    print(f"Workspace: {info['workspace'] or '(sin proyecto)'}")
    print(f"Tickets: {data['total']} (abiertos: {data['abiertos']})")
    return 0


def cmd_attach(args: argparse.Namespace) -> int:
    """Agrupa tickets existentes bajo una épica."""
    with _conn() as conn:
        tickets = repo.attach_tickets(
            conn, args.parent, args.codes, actor=args.agent
        )
    codes = ", ".join(ticket["code"] for ticket in tickets)
    print(
        f"{tickets[0]['parent_code'] or args.parent} es la épica de "
        f"{len(tickets)} tarea(s): {codes}"
    )
    return 0


def cmd_detach(args: argparse.Namespace) -> int:
    """Saca tickets de su épica."""
    with _conn() as conn:
        tickets = repo.detach_tickets(conn, args.codes, actor=args.agent)
    codes = ", ".join(ticket["code"] for ticket in tickets)
    print(f"Sin épica: {codes}")
    return 0
