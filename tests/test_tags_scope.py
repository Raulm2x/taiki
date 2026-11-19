"""Filtros por etiqueta y resolución de ámbito."""

from __future__ import annotations

from pathlib import Path
from ticketlib import repo, util
from tests.seeds import seed_basic
from tests.support import Harness


def run(h: Harness) -> None:
    conn = h.conn
    tmp = str(h.tmp)

    seed_basic(conn)

    # ── 1. Tag Filtering Test Suite ──────────────────────────────────────
    t_noct = repo.create_ticket(
        conn,
        title="Arreglar panel de Noctalia",
        kind="bug",
        priority="alta",
        tags=["noctalia", "ui", "panel"],
    )
    t_hypr = repo.create_ticket(
        conn,
        title="Animación de ventanas en Hyprland",
        kind="funcionalidad",
        priority="media",
        tags=["hyprland", "wm"],
    )
    repo.create_ticket(
        conn,
        title="Mantenimiento general del sistema",
        kind="mantenimiento",
        priority="baja",
        tags=[],
    )
    t_shell = repo.create_ticket(
        conn,
        title="Error en script de shell noctalia",
        kind="bug",
        priority="urgente",
        tags=["shell", "ui"],
    )

    # Tabla de filtros: una fila por combinación, comparada en bloque.
    all_active = repo.list_tickets(conn)
    res_noct = repo.list_tickets(conn, tag="noctalia")
    res_wm = repo.list_tickets(conn, tag="wm")
    res_case = repo.list_tickets(conn, tag="  NOCTALIA  ")
    res_case_hypr = repo.list_tickets(conn, tag="HyPrLaNd")
    res_multi = repo.list_tickets(conn, tags=["noctalia", "wm"])
    res_both = repo.list_tickets(conn, tag="shell", tags=["backup"])
    res_overlap = repo.list_tickets(conn, tags=["noctalia", "ui", "panel"])
    res_tag_status = repo.list_tickets(conn, tag="noctalia", statuses=["en_progreso"])
    res_tag_kind = repo.list_tickets(conn, tag="ui", kind="bug")
    codes_overlap = [ticket["code"] for ticket in res_overlap]
    h.eq(
        (
            len(res_noct),
            all("noctalia" in ticket["tags"] for ticket in res_noct),
            (len(res_wm), res_wm[0]["code"]),
            len(res_case),
            (len(res_case_hypr), res_case_hypr[0]["code"]),
            len(res_multi),
            {ticket["code"] for ticket in res_multi},
            len(res_both),
            {ticket["code"] for ticket in res_both},
            len(codes_overlap) == len(set(codes_overlap)),
            repo.list_tickets(conn, tag="inexistente"),
            repo.list_tickets(conn, tags=["fantasma1", "fantasma2"]),
            len(repo.list_tickets(conn, tag="")),
            len(repo.list_tickets(conn, tags=["   ", ""])),
            (len(res_tag_status), res_tag_status[0]["code"]),
            len(res_tag_kind),
        ),
        (
            2,
            True,
            (1, t_hypr["code"]),
            2,
            (1, t_hypr["code"]),
            3,
            {"T-0001", t_noct["code"], t_hypr["code"]},
            3,
            {"T-0001", "T-0002", t_shell["code"]},
            True,
            [],
            [],
            len(all_active),
            len(all_active),
            (1, "T-0001"),
            2,
        ),
        "los filtros por etiqueta cubren exacta, insensible a mayúsculas "
        "y espacios, múltiple, combinada con tag/tags, sin duplicados, "
        "vacía sin alterar el listado y combinada con estado y tipo",
    )

    # ── 2. Scope Resolution & Git Root Detection Suite ───────────────────
    # Detección de raíz Git real del propio repositorio de Taiki
    project_root = Path(__file__).resolve().parents[1]
    repo_subpath = project_root / "tests"
    file_in_repo = project_root / "cli.py"

    # Simulación de repositorio Git en directorio temporal aislado
    synthetic_repo = Path(tmp) / "mock_project_git"
    (synthetic_repo / ".git").mkdir(parents=True)
    synthetic_deep = synthetic_repo / "src" / "modules" / "sub"
    synthetic_deep.mkdir(parents=True)

    # Soporte para .git como archivo (worktree o submódulo Git)
    wt_repo = Path(tmp) / "mock_worktree"
    wt_repo.mkdir(parents=True)
    (wt_repo / ".git").write_text("gitdir: /tmp/fake_git_dir\n")
    wt_sub = wt_repo / "pkg" / "lib"
    wt_sub.mkdir(parents=True)

    # Directorio sin Git: resuelve al nombre de la carpeta hoja
    plain_isolated = Path(tmp) / "plain_dir" / "leaf_folder"
    plain_isolated.mkdir(parents=True)

    # ``project`` del workspace (.taiki/config.json) manda si no hay --project,
    # y una etiqueta vacía o ausente vuelve a derivar de la raíz Git.
    cfg_proj, _ = util.resolve_scope(None, str(synthetic_deep), {"project": " taiki-sys "})
    cfg_default, _ = util.resolve_scope(None, str(synthetic_deep), {"guard": True})
    cfg_explicit, _ = util.resolve_scope("explicito", str(synthetic_deep), {"project": "x"})
    h.eq(
        (
            util.find_git_root(repo_subpath),
            util.find_git_root(file_in_repo),
            util.resolve_scope(None, str(repo_subpath)),
            util.find_git_root(synthetic_deep),
            util.resolve_scope(None, str(synthetic_deep)),
            util.find_git_root(wt_sub),
            util.find_git_root(Path("/")),
            util.resolve_scope(None, str(plain_isolated)),
            util.resolve_scope("proyecto_personalizado", str(repo_subpath)),
            (cfg_proj, cfg_default, cfg_explicit),
        ),
        (
            project_root,
            project_root,
            (project_root.name, str(repo_subpath)),
            synthetic_repo.resolve(),
            ("mock_project_git", str(synthetic_deep)),
            wt_repo.resolve(),
            None,
            ("leaf_folder", str(plain_isolated)),
            ("proyecto_personalizado", str(repo_subpath)),
            ("taiki-sys", "mock_project_git", "explicito"),
        ),
        "find_git_root resuelve el repo real, archivos y worktrees sintéticos "
        "y devuelve None en la raíz; resolve_scope preserva la ruta, prefiere "
        "el proyecto explícito y usa el del workspace como respaldo",
    )
