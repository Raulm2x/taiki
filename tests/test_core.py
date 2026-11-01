"""Ciclo de vida, commits y listado."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path
import cli
from mcp_layer import handlers
from ticketlib import audit as audit_lib, domain, githook, gitutil, render, repo
from ticketlib.store import workflow
from tui import detail as tui_detail
from tests.seeds import completed, git_repo
from tests.support import Harness


def run(h: Harness) -> None:
    conn = h.conn
    tmp = str(h.tmp)

    ticket = repo.create_ticket(
        conn,
        title="Arreglar arranque de Noctalia",
        body="El shell no recarga la configuración al guardar.",
        kind="bug",
        priority="alta",
        project=".config",
        path=str(Path.home() / ".config"),
        tags=["noctalia", "shell"],
        created_by="agente:test",
    )
    h.ok(ticket["code"] == "T-0001", "el primer código debe ser T-0001")
    h.ok(ticket["status"] == "nuevo", "el estado inicial debe ser nuevo")
    h.ok(ticket["tags"] == ["noctalia", "shell"], "las etiquetas deben guardarse")

    rows = repo.list_tickets(conn, statuses=["nuevo"], project=".config")
    h.ok(len(rows) == 1, "el filtro por estado y proyecto debe devolver 1")

    h.raises(
        domain.TransitionError,
        lambda: repo.transition(conn, "T-0001", "completado"),
        "nuevo → completado debe fallar",
    )

    claimed = repo.claim_ticket(conn, "T-0001", agent="agente:test")
    h.ok(claimed["status"] == "en_progreso", "claim debe pasar a en_progreso")
    h.ok(claimed["assigned_to"] == "agente:test", "claim debe asignar el agente")

    h.raises(
        domain.ValidationError,
        lambda: repo.complete_ticket(conn, "T-0001", resolution="  "),
        "completar sin resolución debe fallar",
    )

    completed = repo.complete_ticket(
        conn,
        "T-0001",
        resolution="Se corrigió la recarga y se validó con noctalia config validate.",
        commit="abc1234",
        files=["noctalia/config.toml"],
    )
    h.ok(completed["status"] == "completado", "complete debe pasar a completado")
    h.ok(completed["completed_at"] is not None, "completed_at debe registrarse")
    h.ok(
        any(link["kind"] == "commit" for link in completed["links"]),
        "el commit debe quedar enlazado",
    )

    h.raises(
        domain.PermissionDenied,
        lambda: repo.transition(conn, "T-0001", "confirmado", actor="agente"),
        "un agente no debe poder confirmar",
    )

    confirmed = repo.transition(
        conn,
        "T-0001",
        "confirmado",
        actor="usuario",
        actor_type="usuario",
    )
    h.ok(confirmed["status"] == "confirmado", "el usuario debe poder confirmar")
    h.ok(confirmed["confirmed_at"] is not None, "confirmed_at debe registrarse")

    h.raises(
        domain.ValidationError,
        lambda: repo.transition(conn, "T-0001", "en_progreso", actor_type="usuario"),
        "reabrir sin motivo debe fallar",
    )

    reopened = repo.transition(
        conn,
        "T-0001",
        "en_progreso",
        note="El fallo reaparece tras reiniciar la sesión",
        actor="usuario",
        actor_type="usuario",
    )
    h.ok(reopened["status"] == "en_progreso", "reabrir debe volver a en_progreso")
    h.ok(reopened["confirmed_at"] is None, "reabrir debe limpiar confirmed_at")
    h.ok(reopened["completed_at"] is None, "reabrir debe limpiar completed_at")

    second = repo.create_ticket(
        conn,
        title="Añadir exportación de respaldos",
        body="Exportar la base a JSON de forma periódica.",
        kind="funcionalidad",
        priority="media",
        project=".config",
        tags=["backup"],
    )
    h.ok(second["code"] == "T-0002", "el segundo código debe ser T-0002")

    found = repo.list_tickets(conn, query="respaldo")
    h.ok(len(found) == 1 and found[0]["code"] == "T-0002", "FTS debe encontrar el ticket")

    repo.comment_ticket(conn, "T-0002", "Usar sqlite3 .backup además del JSON.")
    repo.link_ticket(conn, "T-0002", "url", "https://sqlite.org/backup.html")
    detail = repo.get_ticket(conn, "T-0002")
    h.ok(len(detail["comments"]) == 1, "el comentario debe guardarse")
    h.ok(len(detail["links"]) == 1, "el enlace debe guardarse")

    # ── Autollenazado de commits ────────────────────────────────────────
    auto = repo.auto_link_commit(
        conn, "fix: logout (T-0001) y backup T-0002", "deadbee"
    )
    h.ok(auto["linked"] == ["T-0001", "T-0002"], "auto-link enlaza ambos tickets")
    h.ok(auto["missing"] == [], "auto-link no reporta ausentes si existen")
    detail = repo.get_ticket(conn, "T-0001")
    h.ok(
        any(
            link["kind"] == "commit" and link["ref"] == "deadbee"
            for link in detail["links"]
        ),
        "auto-link debe dejar el commit en el ticket",
    )

    dedup = repo.auto_link_commit(conn, "T-0001 T-0001 T-0001", "cafe123")
    h.ok(dedup["linked"] == ["T-0001"], "auto-link deduplica códigos repetidos")

    missing = repo.auto_link_commit(conn, "T-9999", "beef456")
    h.ok(missing["linked"] == [], "auto-link ignora códigos sin ticket")
    h.ok(missing["missing"] == ["T-9999"], "auto-link reporta códigos ausentes")

    h.raises(
        domain.ValidationError,
        lambda: repo.auto_link_commit(conn, "T-0001", "  "),
        "auto-link exige un commit",
    )

    # Hook post-commit idempotente sobre un repositorio Git real
    hook_repo = Path(tmp) / "hook_repo"
    hook_repo.mkdir()
    subprocess.run(
        ["git", "init", "-q", str(hook_repo)], check=True, capture_output=True
    )
    hook = cli.install_post_commit_hook(hook_repo)
    h.ok(hook.exists(), "hook-install debe crear el post-commit")
    h.ok(os.access(hook, os.X_OK), "el hook debe ser ejecutable")
    h.ok(cli.HOOK_BEGIN in hook.read_text(), "el hook debe tener el bloque gestionado")
    cli.install_post_commit_hook(hook_repo)
    h.ok(
        hook.read_text().count(cli.HOOK_BEGIN) == 1,
        "hook-install no debe duplicar el bloque",
    )
    msg_hook = githook.install_commit_msg_hook(hook_repo)
    h.ok(
        msg_hook.exists() and os.access(msg_hook, os.X_OK),
        "commit-msg se instala y es ejecutable",
    )
    githook.install_commit_msg_hook(hook_repo)
    h.ok(
        msg_hook.read_text().count(githook.COMMIT_MSG_BEGIN) == 1,
        "commit-msg no se duplica",
    )
    push_hook = githook.install_pre_push_hook(hook_repo)
    h.ok(
        push_hook.exists()
        and push_hook.read_text().count(githook.PRE_PUSH_BEGIN) == 1,
        "pre-push se instala una sola vez",
    )
    index_hook = githook.install_pre_commit_hook(hook_repo)
    h.ok(
        index_hook.exists()
        and index_hook.read_text().count(githook.PRE_COMMIT_BEGIN) == 1,
        "pre-commit se instala",
    )
    h.ok(
        githook.hooks_installed(hook_repo)
        == {
            "post-commit": True,
            "commit-msg": True,
            "pre-push": True,
            "pre-commit": True,
        },
        "hooks_installed detecta los cuatro hooks",
    )

    summary = repo.stats(conn)
    h.ok(summary["total"] == 2, "stats debe contar 2 tickets")
    h.ok(summary["abiertos"] == 2, "stats debe contar 2 abiertos")
    h.ok(summary["por_estado"]["en_progreso"] == 1, "stats por estado")

    exported = repo.export_all(conn)
    h.ok(len(exported["tickets"]) == 2, "export debe incluir 2 tickets")
    for exported_ticket in exported["tickets"]:
        h.eq(
            exported_ticket,
            repo.get_ticket(conn, exported_ticket["code"]),
            f"export conserva la forma de get_ticket ({exported_ticket['code']})",
        )

    # ── Commit relacionado: hash completo, dedupe y detalle ────────────
    git_repo = Path(tmp) / "commit_repo"
    git_repo.mkdir()
    subprocess.run(["git", "init", "-q", str(git_repo)], check=True)
    subprocess.run(
        ["git", "-C", str(git_repo), "config", "user.email", "t@t"],
        check=True,
    )
    subprocess.run(
        ["git", "-C", str(git_repo), "config", "user.name", "t"],
        check=True,
    )
    (git_repo / "a.txt").write_text("hola")
    subprocess.run(
        ["git", "-C", str(git_repo), "add", "a.txt"], check=True
    )
    subprocess.run(
        ["git", "-C", str(git_repo), "commit", "-qm", "x"], check=True
    )
    full_sha = gitutil.resolve_commit("HEAD", cwd=git_repo)
    h.ok(
        full_sha is not None and len(full_sha) == 40,
        "resolve_commit devuelve el hash completo",
    )
    h.ok(
        gitutil.resolve_commit("deadbeef", cwd=git_repo) is None,
        "resolve_commit no inventa commits",
    )

    with_commit = repo.create_ticket(conn, title="Con commit")
    repo.claim_ticket(conn, with_commit["code"], agent="test")
    repo.complete_ticket(conn, with_commit["code"], resolution="ok")
    from ticketlib.store import relations as rel

    original_resolve = rel.resolve_commit
    rel.resolve_commit = lambda ref: None
    try:
        repo.link_ticket(
            conn, with_commit["code"], "commit", "abc1234", actor="test"
        )
        repo.link_ticket(
            conn,
            with_commit["code"],
            "commit",
            "abc1234def5678",
            actor="test",
        )
    finally:
        rel.resolve_commit = original_resolve
    h.ok(
        repo.get_ticket(conn, with_commit["code"])["commits"]
        == ["abc1234def5678"],
        "el commit corto se reemplaza por el completo sin duplicar",
    )
    detail_commit = tui_detail.detail_lines(
        repo.get_ticket(conn, with_commit["code"]), 70
    )
    h.ok(
        any(line.text.startswith("Commit") for line in detail_commit),
        "el detalle de la TUI muestra el commit",
    )

    without_commit = repo.create_ticket(conn, title="Sin commit")
    repo.claim_ticket(conn, without_commit["code"], agent="test")
    repo.complete_ticket(conn, without_commit["code"], resolution="ok")
    pendientes = {
        item["code"] for item in audit_lib.uncommitted(conn)
    }
    h.ok(
        without_commit["code"] in pendientes
        and with_commit["code"] not in pendientes,
        "audit distingue los cerrados sin commit",
    )

    # Relleno: el bloque de paginación necesita varias filas.
    for index in range(3):
        repo.create_ticket(conn, title=f"Ticket de relleno {index}")

    # ── Listado: orden descendente, paginación y columna de agente ──────
    all_codes = [t["code"] for t in repo.list_tickets(conn, limit=None)]
    h.ok(
        all_codes == sorted(all_codes, reverse=True),
        "list_tickets ordena por código descendente por defecto",
    )

    page1 = repo.list_tickets(conn, limit=3, offset=0)
    page2 = repo.list_tickets(conn, limit=3, offset=3)
    h.ok(len(page1) == 3 and len(page2) == 3, "limit/offset devuelven páginas de 3")
    h.ok(
        not ({t["code"] for t in page1} & {t["code"] for t in page2}),
        "las páginas no se solapan",
    )
    h.ok(
        [t["code"] for t in page1 + page2] == all_codes[:6],
        "las páginas conservan el orden global",
    )

    total_open = repo.count_tickets(conn)
    h.ok(
        total_open == len(repo.list_tickets(conn, limit=None)),
        "count_tickets coincide con el listado completo",
    )
    h.ok(
        repo.count_tickets(conn, statuses=["en_progreso"]) == len(
            repo.list_tickets(conn, statuses=["en_progreso"], limit=None)
        ),
        "count_tickets respeta los filtros",
    )

    assigned = [t for t in repo.list_tickets(conn, limit=None) if t["assigned_to"]]
    h.ok(assigned, "hay tickets asignados para comprobar la tabla")
    table = render.render_table([assigned[0]], use_color=False)
    h.ok(
        "AGENTE" in table and assigned[0]["assigned_to"] in table,
        "la tabla muestra el agente encargado",
    )

    prioritized = repo.list_tickets(conn, order="priority", limit=None)
    h.ok(
        len(prioritized) == len(all_codes),
        "order='priority' devuelve el mismo conjunto de tickets",
    )
    h.raises(
        domain.ValidationError,
        lambda: repo.list_tickets(conn, order="inventado"),
        "un orden desconocido debe fallar",
    )

    open_total = repo.count_tickets(
        conn, statuses=list(domain.OPEN_STATUSES)
    )
    mcp_page = handlers.call_tool("list", {"limit": 2, "offset": 1})
    h.ok(
        mcp_page["total"] == open_total
        and mcp_page["offset"] == 1
        and mcp_page["count"] == 2,
        "call_tool('list') devuelve total abierto y respeta offset",
    )
    mcp_all = handlers.call_tool(
        "list", {"limit": 2, "offset": 1, "include_closed": True}
    )
    h.ok(
        mcp_all["total"] == total_open,
        "call_tool('list') con include_closed cuenta todos",
    )

    # ── List sin body salvo que se pida detalle ──────────────────────────
    lean = repo.list_tickets(conn, limit=None)
    h.ok(
        lean and all("body" not in ticket for ticket in lean),
        "list omite body/resolution sin detail",
    )
    detailed = repo.list_tickets(conn, limit=None, detail=True)
    h.ok(
        all(
            "body" in ticket and "resolution" in ticket
            for ticket in detailed
        ),
        "list con detail=True conserva body/resolution",
    )
    mcp_lean = handlers.call_tool("list", {"limit": 5})
    h.ok(
        mcp_lean["tickets"]
        and all("body" not in ticket for ticket in mcp_lean["tickets"]),
        "list MCP no arrastra body en resumen",
    )
    mcp_detail = handlers.call_tool("list", {"limit": 5, "detail": True})
    h.ok(
        mcp_detail["tickets"]
        and all("body" in ticket for ticket in mcp_detail["tickets"]),
        "list MCP con detail=true incluye body",
    )

    # ── complete_ticket es atómico: sin enlaces no hay cierre ────────────
    atomic = repo.create_ticket(conn, title="Cierre atómico")
    repo.claim_ticket(conn, atomic["code"], agent="agente:test")
    original_link = workflow.link_ticket

    def broken_link(*args, **kwargs):
        raise RuntimeError("fallo simulado al enlazar")

    workflow.link_ticket = broken_link
    try:
        h.raises(
            RuntimeError,
            lambda: repo.complete_ticket(
                conn,
                atomic["code"],
                resolution="no debe quedar",
                commit="deadbee",
            ),
            "un fallo al enlazar aborta el cierre",
        )
    finally:
        workflow.link_ticket = original_link
    after = repo.get_ticket(conn, atomic["code"])
    h.ok(
        after["status"] == "en_progreso"
        and not after["commits"]
        and after["completed_at"] is None,
        "el fallo no deja el ticket completado sin enlaces",
    )
