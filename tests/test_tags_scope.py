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
    t_plain = repo.create_ticket(
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

    # Filtro por etiqueta única (exacta)
    res_noct = repo.list_tickets(conn, tag="noctalia")
    h.ok(len(res_noct) == 2, "filtro tag='noctalia' debe encontrar T-0001 y T-0003")
    h.ok(all("noctalia" in t["tags"] for t in res_noct), "todos los devueltos deben contener la etiqueta 'noctalia'")

    res_wm = repo.list_tickets(conn, tag="wm")
    h.ok(len(res_wm) == 1 and res_wm[0]["code"] == t_hypr["code"], "filtro tag='wm' debe encontrar exactamente T-0004")

    # Insensibilidad a mayúsculas/minúsculas y recorte de espacios
    res_case = repo.list_tickets(conn, tag="  NOCTALIA  ")
    h.ok(len(res_case) == 2, "filtro de etiqueta debe ser insensible a mayúsculas y espacios")
    res_case_hypr = repo.list_tickets(conn, tag="HyPrLaNd")
    h.ok(len(res_case_hypr) == 1 and res_case_hypr[0]["code"] == t_hypr["code"], "filtro 'HyPrLaNd' coincide con 'hyprland'")

    # Filtro por múltiples etiquetas (coincidencia con cualquiera)
    res_multi = repo.list_tickets(conn, tags=["noctalia", "wm"])
    h.ok(len(res_multi) == 3, "filtro tags=['noctalia', 'wm'] debe devolver 3 tickets")
    h.ok(set(t["code"] for t in res_multi) == {"T-0001", t_noct["code"], t_hypr["code"]}, "códigos esperados en tags múltiples")

    # Filtro combinando tag y tags simultáneamente
    res_both = repo.list_tickets(conn, tag="shell", tags=["backup"])
    h.ok(len(res_both) == 3, "combinación de tag='shell' y tags=['backup'] debe encontrar 3 tickets")
    h.ok(set(t["code"] for t in res_both) == {"T-0001", "T-0002", t_shell["code"]}, "códigos esperados en tag + tags")

    # Cero filas duplicadas cuando un ticket coincide con múltiples etiquetas filtradas
    res_overlap = repo.list_tickets(conn, tags=["noctalia", "ui", "panel"])
    codes_overlap = [t["code"] for t in res_overlap]
    h.ok(len(codes_overlap) == len(set(codes_overlap)), "cero filas duplicadas cuando un ticket coincide con varios tags")
    h.ok(codes_overlap.count(t_noct["code"]) == 1, "t_noct aparece exactamente una vez")

    # Etiqueta inexistente
    res_empty = repo.list_tickets(conn, tag="inexistente")
    h.ok(len(res_empty) == 0, "etiqueta inexistente debe devolver lista vacía")
    res_empty_multi = repo.list_tickets(conn, tags=["fantasma1", "fantasma2"])
    h.ok(len(res_empty_multi) == 0, "múltiples etiquetas inexistentes deben devolver lista vacía")

    # Etiquetas vacías o espacios no deben fallar ni filtrar indebidamente
    all_active = repo.list_tickets(conn)
    res_blank = repo.list_tickets(conn, tag="")
    h.ok(len(res_blank) == len(all_active), "tag='' no debe alterar la cuenta de tickets activos")
    res_blank_tags = repo.list_tickets(conn, tags=["   ", ""])
    h.ok(len(res_blank_tags) == len(all_active), "tags con espacios en blanco no deben alterar el listado")

    # Combinación de filtro por etiqueta con otros filtros (estado, tipo)
    res_tag_status = repo.list_tickets(conn, tag="noctalia", statuses=["en_progreso"])
    h.ok(len(res_tag_status) == 1 and res_tag_status[0]["code"] == "T-0001", "filtro tag + status encuentra solo T-0001")
    res_tag_kind = repo.list_tickets(conn, tag="ui", kind="bug")
    h.ok(len(res_tag_kind) == 2, "filtro tag='ui' + kind='bug' devuelve 2 tickets")

    # ── 2. Scope Resolution & Git Root Detection Suite ───────────────────
    # Detección de raíz Git real del propio repositorio de Taiki
    project_root = Path(__file__).resolve().parents[1]
    repo_subpath = project_root / "tests"
    git_root_found = util.find_git_root(repo_subpath)
    h.ok(git_root_found is not None, "find_git_root encuentra la raíz Git del proyecto")
    h.ok(git_root_found == project_root, "la raíz Git de tests/ es la del proyecto")

    # Archivo dentro del repositorio
    file_in_repo = project_root / "cli.py"
    h.ok(util.find_git_root(file_in_repo) == project_root, "find_git_root sobre archivo resuelve al repo padre")

    # resolve_scope en subdirectorio de repositorio Git asigna el proyecto al repo raíz
    scope_proj, scope_path = util.resolve_scope(None, str(repo_subpath))
    h.ok(scope_proj == project_root.name, "resolve_scope en subdirectorio de repo debe asignar proyecto a la raíz Git")
    h.ok(scope_path == str(repo_subpath), "resolve_scope preserva la ruta especificada")

    # Simulación de repositorio Git en directorio temporal aislado
    synthetic_repo = Path(tmp) / "mock_project_git"
    (synthetic_repo / ".git").mkdir(parents=True)
    synthetic_deep = synthetic_repo / "src" / "modules" / "sub"
    synthetic_deep.mkdir(parents=True)

    synth_root = util.find_git_root(synthetic_deep)
    h.ok(synth_root == synthetic_repo.resolve(), "find_git_root resuelve repositorio sintético")
    synth_proj, synth_path = util.resolve_scope(None, str(synthetic_deep))
    h.ok(synth_proj == "mock_project_git", "resolve_scope sobre repositorio sintético extrae nombre del repo")
    h.ok(synth_path == str(synthetic_deep), "resolve_scope preserva ruta del subdirectorio sintético")

    # Soporte para .git como archivo (worktree o submódulo Git)
    wt_repo = Path(tmp) / "mock_worktree"
    wt_repo.mkdir(parents=True)
    (wt_repo / ".git").write_text("gitdir: /tmp/fake_git_dir\n")
    wt_sub = wt_repo / "pkg" / "lib"
    wt_sub.mkdir(parents=True)
    h.ok(util.find_git_root(wt_sub) == wt_repo.resolve(), "find_git_root reconoce archivo .git de worktrees")

    # Directorio sin Git: resuelve al nombre de la carpeta hoja
    plain_isolated = Path(tmp) / "plain_dir" / "leaf_folder"
    plain_isolated.mkdir(parents=True)
    h.ok(util.find_git_root(Path("/")) is None, "find_git_root en raíz del sistema devuelve None")
    plain_proj, plain_path = util.resolve_scope(None, str(plain_isolated))
    h.ok(plain_proj == "leaf_folder", "directorio sin Git debe resolver al nombre del directorio hoja")

    # Sobrescritura explícita de proyecto: tiene prioridad sobre la detección Git
    exp_proj, exp_path = util.resolve_scope("proyecto_personalizado", str(repo_subpath))
    h.ok(exp_proj == "proyecto_personalizado", "proyecto explícito prevalece sobre la detección automática")
    h.ok(exp_path == str(repo_subpath), "ruta se mantiene con proyecto explícito")
