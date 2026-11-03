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

    info = workspace.init_workspace(project, name="Proyecto de prueba")
    folder = project / ".taiki"
    h.eq(
        (
            info["workspace"],
            (folder / "taiki.db").is_file(),
            (folder / "config.json").is_file(),
            (folder / ".gitignore").is_file(),
        ),
        (folder, True, True, True),
        "init_workspace crea .taiki con DB, configuración y .gitignore",
    )

    with h.env(HOME=str(home), TAIKI_DB=None, TICKETS_DB=None):
        resolved = workspace.resolve(nested)
        h.eq(
            (
                workspace.find_workspace(nested),
                workspace.find_workspace(home / "otro"),
                resolved["source"],
                resolved["project"],
                workspace.label(nested),
                workspace.resolve_db(nested),
            ),
            (folder, None, "project", "proyecto", "proyecto", folder / "taiki.db"),
            "find_workspace sube al .taiki y no hereda ~/.taiki; resolve/label/DB coinciden",
        )

    # ── Configuración ───────────────────────────────────────────────────
    config = workspace.load_config(folder)
    config["defaults"]["priority"] = "alta"
    workspace.save_config(config, folder)
    saved = workspace.load_config(folder)
    h.eq(
        (
            saved["project"],
            saved["defaults"],
            saved["guard"],
            saved["guard_auto"],
        ),
        ("proyecto", {"kind": "otro", "priority": "alta"}, False, "scratch"),
        "config.json trae defaults razonables, guard opt-in y save_config persiste",
    )

    # ── Precedencia de la base de datos ──────────────────────────────────
    with h.env(
        HOME=str(home),
        XDG_DATA_HOME=str(tmp / "xdg"),
        TAIKI_DB=None,
        TICKETS_DB=None,
    ):
        missing = workspace.resolve(home / "otro")
        try:
            workspace.resolve_db(home / "otro")
        except domain.TicketError:
            needs_init = True
        else:
            needs_init = False
        with h.env(TAIKI_DB=str(tmp / "explicita.db"), TICKETS_DB=None):
            explicit = workspace.resolve_db(nested)
        with h.env(TAIKI_DB=None, TICKETS_DB=str(tmp / "alias.db")):
            alias = workspace.resolve_db(nested)
        with h.env(
            TAIKI_DB=str(tmp / "nueva.db"),
            TICKETS_DB=str(tmp / "alias.db"),
        ):
            both = workspace.resolve_db(nested)
        h.eq(
            (
                missing["source"],
                missing["db"],
                needs_init,
                explicit,
                alias,
                both,
                workspace.registry_path(),
            ),
            (
                "missing",
                None,
                True,
                tmp / "explicita.db",
                tmp / "alias.db",
                tmp / "nueva.db",
                tmp / "xdg" / "taiki" / "registry.db",
            ),
            "sin .taiki resolve_db pide init; TAIKI_DB gana, alias y registro XDG",
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
    conn = db.connect(adopted["db"])
    try:
        codes = [ticket["code"] for ticket in repo.list_tickets(conn)]
    finally:
        conn.close()
    try:
        workspace.init_workspace(other, adopt=source)
    except domain.TicketError:
        re_adopt = True
    else:
        re_adopt = False
    with h.env(TAIKI_DB=str(adopted["db"])):
        h.eq(
            (
                adopted["adopted"],
                created["code"] in codes,
                re_adopt,
                db.db_path(),
            ),
            (True, True, True, adopted["db"]),
            "adopción conserva tickets, sin --force no pisa y db_path respeta el override",
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

    config["guard"] = True
    workspace.save_config(config, folder)
    with h.env(TICKETS_DB=None, TAIKI_DB=None):
        on = guard.evaluate(
            tool="write",
            path=str(Path.home() / "taiki-guard-test.txt"),
            cwd=str(project),
        )
    h.eq(
        (off["action"], on["action"] in ("created", "claimed", "blocked")),
        ("disabled", True),
        "el guard es opt-in: sin guard:true no interviene; con él crea o reclama",
    )

    # ── El grafo de imports de ticketlib no tiene ciclos ────────────────
    import ast

    root = Path(__file__).resolve().parent.parent / "ticketlib"
    modules = {
        ".".join(path.relative_to(root.parent).with_suffix("").parts): path
        for path in root.rglob("*.py")
    }
    graph: dict[str, set[str]] = {name: set() for name in modules}
    for name, path in modules.items():
        package = name.rsplit(".", 1)[0] if "." in name else name
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                base = node.module or ""
                if node.level:
                    parts = package.split(".")
                    if node.level > 1:
                        parts = parts[: -(node.level - 1)]
                    base = ".".join([*parts, base]).strip(".")
                if base in modules and base != name:
                    graph[name].add(base)
                for alias in node.names:
                    target = f"{base}.{alias.name}" if base else alias.name
                    if target in modules and target != name:
                        graph[name].add(target)
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name in modules and alias.name != name:
                        graph[name].add(alias.name)

    visiting: set[str] = set()
    done: set[str] = set()
    cycle: list[str] = []

    def walk(module: str, stack: list[str]) -> bool:
        if module in visiting:
            cycle.extend([*stack, module])
            return True
        if module in done:
            return False
        visiting.add(module)
        for target in graph.get(module, ()):
            if walk(target, [*stack, module]):
                return True
        visiting.remove(module)
        done.add(module)
        return False

    for module in graph:
        if walk(module, []):
            break
    h.eq(cycle, [], "el grafo de imports de ticketlib no tiene ciclos")

    # ── Fallback de DB explícito ────────────────────────────────────────
    original_mkdir = Path.mkdir
    original_tempdir = db.tempfile.gettempdir

    def failing_mkdir(self, *args, **kwargs):
        if str(self).endswith("sin-permiso"):
            raise PermissionError("sin permisos")
        return original_mkdir(self, *args, **kwargs)

    Path.mkdir = failing_mkdir
    db.tempfile.gettempdir = lambda: str(tmp)
    try:
        try:
            db.connect(tmp / "sin-permiso" / "tickets.db")
        except domain.TicketError:
            blocked = True
        else:
            blocked = False
        with h.env(TAIKI_TMP_DB="1"):
            fallback = db.connect(tmp / "sin-permiso" / "tickets.db")
            fallback.close()
        h.ok(
            blocked,
            "sin permisos la DB falla con TicketError; TAIKI_TMP_DB=1 permite el fallback",
        )
    finally:
        Path.mkdir = original_mkdir
        db.tempfile.gettempdir = original_tempdir
