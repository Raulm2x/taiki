"""Resolución de workspaces .taiki, configuración e inicialización."""

from __future__ import annotations

from pathlib import Path

from ticketlib import db, domain, guard, repo, workspace
from tests.support import Harness


def run(h: Harness) -> None:
    tmp = Path(h.tmp)
    home = tmp / "home"
    project = home / "proyecto"
    nested = project / "src" / "modulo"
    nested.mkdir(parents=True)

    # ── Descubrimiento del workspace ────────────────────────────────────
    (home / ".taiki").mkdir(parents=True)
    (home / "otro").mkdir(parents=True)
    with h.env(HOME=str(home), TAIKI_DB=None, TICKETS_DB=None):
        h.eq(
            workspace.find_workspace(home / "otro"),
            None,
            "la búsqueda se detiene antes de $HOME y no hereda ~/.taiki",
        )

    info = workspace.init_workspace(project, name="Proyecto de prueba")
    folder = project / ".taiki"
    h.eq(info["workspace"], folder, "init_workspace crea .taiki en la raíz")
    h.ok((folder / "taiki.db").is_file(), "init_workspace crea taiki.db")
    h.ok((folder / "config.json").is_file(), "init_workspace crea config.json")
    h.ok((folder / ".gitignore").is_file(), "init_workspace crea .gitignore")

    with h.env(HOME=str(home), TAIKI_DB=None, TICKETS_DB=None):
        h.eq(
            workspace.find_workspace(nested),
            folder,
            "find_workspace sube hasta el .taiki del proyecto",
        )
        resolved = workspace.resolve(nested)
        h.eq(resolved["source"], "project", "resolve marca el origen del proyecto")
        h.eq(resolved["project"], "proyecto", "resolve extrae el nombre del proyecto")
        h.eq(
            workspace.label(nested),
            "proyecto",
            "label devuelve el nombre del proyecto para la cabecera",
        )
        h.eq(
            workspace.resolve_db(nested),
            folder / "taiki.db",
            "la DB del proyecto gana sobre la global",
        )

    # ── Configuración ───────────────────────────────────────────────────
    config = workspace.load_config(folder)
    h.eq(config["project"], "proyecto", "config.json guarda el proyecto")
    h.eq(
        config["defaults"],
        {"kind": "otro", "priority": "media"},
        "config.json trae defaults razonables",
    )
    h.eq(config.get("guard"), False, "el guard es opt-in por defecto")
    h.eq(
        config.get("guard_auto"),
        "scratch",
        "el guard solo crea temporales por defecto",
    )
    config["defaults"]["priority"] = "alta"
    workspace.save_config(config, folder)
    h.eq(
        workspace.load_config(folder)["defaults"]["priority"],
        "alta",
        "save_config persiste cambios",
    )

    # ── Precedencia de la base de datos ──────────────────────────────────
    with h.env(
        HOME=str(home),
        XDG_DATA_HOME=str(tmp / "xdg"),
        TAIKI_DB=None,
        TICKETS_DB=None,
    ):
        missing = workspace.resolve(home / "otro")
        h.eq(missing["source"], "missing", "sin .taiki el origen es missing")
        h.eq(missing["db"], None, "sin .taiki no hay DB implícita")
        h.raises(
            domain.TicketError,
            lambda: workspace.resolve_db(home / "otro"),
            "sin .taiki resolve_db pide inicializar",
        )
        with h.env(TAIKI_DB=str(tmp / "explicita.db")):
            h.eq(
                workspace.resolve_db(nested),
                tmp / "explicita.db",
                "TAIKI_DB tiene prioridad sobre el proyecto",
            )
        with h.env(TAIKI_DB=None, TICKETS_DB=str(tmp / "alias.db")):
            h.eq(
                workspace.resolve_db(nested),
                tmp / "alias.db",
                "TICKETS_DB se mantiene como alias",
            )
        with h.env(
            TAIKI_DB=str(tmp / "nueva.db"),
            TICKETS_DB=str(tmp / "alias.db"),
        ):
            h.eq(
                workspace.resolve_db(nested),
                tmp / "nueva.db",
                "TAIKI_DB gana sobre el alias TICKETS_DB",
            )

        h.eq(
            workspace.registry_path(),
            tmp / "xdg" / "taiki" / "registry.db",
            "el registro de instancias respeta XDG_DATA_HOME",
        )

    # ── Adopción de una base existente ───────────────────────────────────
    source = tmp / "heredada.db"
    conn = db.connect(source)
    try:
        created = repo.create_ticket(conn, title="Ticket heredado")
    finally:
        conn.close()

    other = home / "adoptado"
    other.mkdir(parents=True)
    adopted = workspace.init_workspace(other, adopt=source)
    h.ok(adopted["adopted"], "init_workspace informa de la adopción")
    conn = db.connect(adopted["db"])
    try:
        codes = [ticket["code"] for ticket in repo.list_tickets(conn)]
    finally:
        conn.close()
    h.ok(created["code"] in codes, "la adopción conserva los tickets")

    h.raises(
        domain.TicketError,
        lambda: workspace.init_workspace(other, adopt=source),
        "adoptar de nuevo sin --force no pisa la DB",
    )
    with h.env(TAIKI_DB=str(adopted["db"])):
        h.eq(
            db.db_path(),
            adopted["db"],
            "db_path respeta el override explícito",
        )

    # ── Guard opt-in ─────────────────────────────────────────────────────
    config = workspace.load_config(folder)
    config["guard"] = False
    workspace.save_config(config, folder)
    with h.env(TICKETS_DB=None, TAIKI_DB=None):
        off = guard.evaluate(
            tool="write",
            path=str(Path.home() / "taiki-guard-test.txt"),
            cwd=str(project),
        )
        h.eq(off["action"], "disabled", "sin guard:true el guard no interviene")

    config["guard"] = True
    workspace.save_config(config, folder)
    with h.env(TICKETS_DB=None, TAIKI_DB=None):
        on = guard.evaluate(
            tool="write",
            path=str(Path.home() / "taiki-guard-test.txt"),
            cwd=str(project),
        )
        h.ok(
            on["action"] in ("created", "claimed", "blocked"),
            "con guard:true el guard crea o reclama el ticket",
        )
