"""Integración con herdr: abrir el agente de un ticket en su workspace.

herdr expone la sesión de cada pane (`agent_session.value`), pero no el
directorio de la workspace. El directorio se deduce de los `cwd` de sus panes:
coincidencia exacta o el ancestro más profundo, para no abrir una pestaña en
`~` cuando el ticket vive en otro directorio.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any, Callable, cast

TIMEOUT = 15


class HerdrError(Exception):
    """herdr no está disponible o devolvió algo inesperado."""


def binary() -> str | None:
    override = os.environ.get("HERDR_BIN_PATH")
    if override and Path(override).expanduser().exists():
        return str(Path(override).expanduser())
    return shutil.which("herdr")


def socket_path() -> Path:
    override = os.environ.get("HERDR_SOCKET_PATH")
    if override:
        return Path(override).expanduser()
    return Path.home() / ".config" / "herdr" / "herdr.sock"


def available() -> bool:
    """herdr es usable si hay binario y sesión: dentro del pane o vía socket.

    La TUI puede lanzarse desde el lanzador (kitty) y no hereda HERDR_ENV; el
    CLI de herdr sigue funcionando contra el socket, así que también cuenta.
    """
    if binary() is None:
        return False
    return os.environ.get("HERDR_ENV") == "1" or socket_path().exists()


def _default_runner(argv: list[str]) -> dict[str, Any]:
    executable = binary()
    if not executable:
        raise HerdrError("herdr no está instalado")
    try:
        result = subprocess.run(
            [executable, *argv],
            capture_output=True,
            text=True,
            timeout=TIMEOUT,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise HerdrError(f"herdr falló: {exc}") from exc
    if result.returncode != 0:
        detail = result.stderr.strip() or result.stdout.strip()
        raise HerdrError(f"herdr {' '.join(argv)}: {detail}")
    try:
        data = json.loads(result.stdout or "{}")
    except json.JSONDecodeError as exc:
        raise HerdrError(f"herdr devolvió JSON inválido: {exc}") from exc
    if not isinstance(data, dict):
        return {}
    return cast(dict[str, Any], data)


def _payload(data: dict[str, Any]) -> dict[str, Any]:
    result = data.get("result")
    if isinstance(result, dict):
        return cast(dict[str, Any], result)
    return data


def agents(run: Callable[[list[str]], dict[str, Any]]) -> list[dict[str, Any]]:
    return _payload(run(["agent", "list"])).get("agents") or []


def panes(run: Callable[[list[str]], dict[str, Any]]) -> list[dict[str, Any]]:
    return _payload(run(["pane", "list"])).get("panes") or []


def workspaces(run: Callable[[list[str]], dict[str, Any]]) -> list[dict[str, Any]]:
    return _payload(run(["workspace", "list"])).get("workspaces") or []


def tabs(run: Callable[[list[str]], dict[str, Any]], workspace: str) -> list[dict[str, Any]]:
    return _payload(
        run(["tab", "list", "--workspace", workspace])
    ).get("tabs") or []


def live_pane(agents_list: list[dict[str, Any]], session_id: str | None) -> dict[str, Any] | None:
    """Pane que ya está sirviendo esa sesión (evita duplicados)."""
    if not session_id:
        return None
    for agent in agents_list:
        info = agent.get("agent_session") or {}
        if info.get("value") == session_id:
            return agent
    return None


def _covers(base: str, target: str) -> bool:
    base = base.rstrip("/") or "/"
    return target == base or target.startswith(base + "/")


def deepest_workspace(
    panes_list: list[dict[str, Any]],
    target: str,
    workspaces_list: list[dict[str, Any]] | None = None,
) -> str | None:
    """Workspace cuyo pane esté más cerca del directorio del ticket."""
    try:
        resolved = str(Path(target).expanduser().resolve())
    except (OSError, RuntimeError):
        resolved = str(target)
    best: tuple[int, str] | None = None
    for pane in panes_list:
        cwd = pane.get("cwd")
        workspace = pane.get("workspace_id")
        if not cwd or not workspace:
            continue
        try:
            base = str(Path(cwd).expanduser().resolve())
        except (OSError, RuntimeError):
            base = str(cwd)
        if _covers(base, resolved):
            score = len(base)
            if best is None or score > best[0]:
                best = (score, workspace)
    if best is not None:
        return best[1]
    if workspaces_list:
        return None
    return None


def tab_label(code: str, title: str = "") -> str:
    """Etiqueta de la pestaña: solo el código, el título la haría ilegible."""
    return code


def find_tab(
    tabs_list: list[dict[str, Any]], code: str
) -> dict[str, Any] | None:
    for tab in tabs_list:
        label = str(tab.get("label") or "")
        if label == code or label.startswith(code + " "):
            return tab
    return None


def free_pane(
    panes_list: list[dict[str, Any]], tab_id: str
) -> dict[str, Any] | None:
    """Primer pane de la pestaña sin agente (shell disponible)."""
    for pane in panes_list:
        if pane.get("tab_id") == tab_id and not pane.get("agent"):
            return pane
    return None


def agent_name(code: str) -> str:
    name = re.sub(r"[^a-z0-9_-]+", "-", code.lower()).strip("-")
    return name[:32] or "ticket"


def kind_for(backend: str | None) -> str:
    return "agy" if backend == "antigravity" else "opencode"


def resume_args(backend: str | None, session_id: str) -> list[str]:
    if backend == "antigravity":
        return ["--conversation", session_id]
    return ["--session", session_id]


def prompt_text(code: str, title: str) -> str:
    return (
        f"Trabaja el ticket {code}: {title}. "
        f"Reclámalo con claim si no está en progreso y, al terminar, "
        f"ciérralo con complete."
    )


def plan(
    *,
    code: str,
    title: str,
    path: str,
    session_id: str | None,
    backend: str | None,
    force_new: bool = False,
    space: str = "auto",
    run: Callable[[list[str]], dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Lee el estado y decide qué haría falta (sin mutar nada)."""
    run = run or _default_runner
    if session_id and not force_new:
        live = live_pane(agents(run), session_id)
        if live:
            return {
                "action": "focus",
                "pane_id": live.get("pane_id"),
                "tab_id": live.get("tab_id"),
                "session_id": session_id,
                "steps": [["agent", "focus", str(live.get("pane_id"))]],
            }

    panes_list = panes(run)
    if space == "current":
        workspace = os.environ.get("HERDR_WORKSPACE_ID")
    else:
        workspace = deepest_workspace(panes_list, path, workspaces(run))

    steps: list[list[str]] = []
    if not workspace:
        workspace = "<nuevo>"
        steps.append(
            [
                "workspace",
                "create",
                "--cwd",
                path,
                "--label",
                Path(path).name or "tickets",
                "--no-focus",
            ]
        )

    tab = None
    pane: Any = None
    if workspace != "<nuevo>":
        tab = find_tab(tabs(run, workspace), code)
        if tab:
            pane = free_pane(panes_list, str(tab.get("tab_id")))

    if tab is None or pane is None:
        steps.append(
            [
                "tab",
                "create",
                "--workspace",
                workspace,
                "--cwd",
                path,
                "--label",
                tab_label(code, title),
                "--no-focus",
            ]
        )
        pane = "<pane>"

    name = agent_name(code)
    start = ["agent", "start", name, "--kind", kind_for(backend), "--pane", str(pane)]
    if session_id:
        start += ["--", *resume_args(backend, session_id)]
    steps.append(start)
    if not session_id:
        steps.append(["agent", "prompt", name, prompt_text(code, title)])
    steps.append(["agent", "focus", str(pane)])

    return {
        "action": "open",
        "workspace_id": None if workspace == "<nuevo>" else workspace,
        "tab_id": tab.get("tab_id") if tab else None,
        "pane_id": None if pane == "<pane>" else pane,
        "session_id": session_id,
        "steps": steps,
    }


def open_ticket(
    *,
    code: str,
    title: str,
    path: str,
    session_id: str | None,
    backend: str | None,
    force_new: bool = False,
    space: str = "auto",
    focus: bool = True,
    dry_run: bool = False,
    run: Callable[[list[str]], dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Ejecuta el plan de `plan()` (o lo devuelve si ``dry_run``)."""
    run = run or _default_runner
    decision = plan(
        code=code,
        title=title,
        path=path,
        session_id=session_id,
        backend=backend,
        force_new=force_new,
        space=space,
        run=run,
    )
    if decision["action"] == "focus":
        if not dry_run:
            run(["agent", "focus", str(decision["pane_id"])])
        return decision
    if dry_run:
        return decision

    workspace = decision["workspace_id"]
    if workspace is None:
        created = _payload(
            run(
                [
                    "workspace",
                    "create",
                    "--cwd",
                    path,
                    "--label",
                    Path(path).name or "tickets",
                    "--no-focus",
                ]
            )
        )
        workspace = (created.get("workspace") or {}).get("workspace_id")
        pane_id = (created.get("root_pane") or {}).get("pane_id")
        tab_id = (created.get("tab") or {}).get("tab_id")
        if not workspace or not pane_id:
            raise HerdrError("herdr no devolvió workspace/root pane al crear")
        tab = {"tab_id": tab_id}
        pane = {"pane_id": pane_id}
    else:
        tab = None
        pane = None
        existing = find_tab(tabs(run, workspace), code)
        if existing:
            tab = existing
            pane = free_pane(panes(run), str(existing.get("tab_id")))

    if not tab or not pane:
        created = _payload(
            run(
                [
                    "tab",
                    "create",
                    "--workspace",
                    workspace,
                    "--cwd",
                    path,
                    "--label",
                    tab_label(code, title),
                    "--no-focus",
                ]
            )
        )
        tab = created.get("tab") or {}
        pane = created.get("root_pane") or {}
        if not pane.get("pane_id") or not tab.get("tab_id"):
            raise HerdrError("herdr no devolvió tab/root pane al crear")

    name = agent_name(code)
    start = [
        "agent",
        "start",
        name,
        "--kind",
        kind_for(backend),
        "--pane",
        str(pane["pane_id"]),
    ]
    if session_id:
        start += ["--", *resume_args(backend, session_id)]
    run(start)

    if not session_id:
        run(["agent", "prompt", name, prompt_text(code, title)])

    if focus:
        run(["agent", "focus", str(pane["pane_id"])])

    return {
        "action": "opened",
        "workspace_id": workspace,
        "tab_id": tab.get("tab_id"),
        "pane_id": pane.get("pane_id"),
        "agent": name,
        "session_id": session_id,
        "steps": decision["steps"],
    }


def open_and_link(
    conn: Any,
    ticket: dict[str, Any],
    session_id: str | None,
    *,
    backend: str | None,
    directory: str,
    force_new: bool = False,
    space: str = "auto",
    focus: bool = True,
    dry_run: bool = False,
    wait: float = 10.0,
    run: Callable[[list[str]], dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Abre el ticket y enlaza la sesión nueva, si herdr la reporta."""
    from . import repo

    result = open_ticket(
        code=ticket["code"],
        title=ticket.get("title", ""),
        path=directory,
        session_id=session_id,
        backend=backend,
        force_new=force_new,
        space=space,
        focus=focus,
        dry_run=dry_run,
        run=run,
    )
    if dry_run or result["action"] == "focus":
        return result

    reported = result.get("session_id")
    if not reported:
        reported = wait_session(str(result["pane_id"]), timeout=wait, run=run)
        result["session_id"] = reported
    if reported and reported != session_id:
        repo.link_ticket(
            conn,
            ticket["code"],
            "sesion",
            reported,
            actor="usuario",
            actor_type="usuario",
        )
        repo.comment_ticket(
            conn,
            ticket["code"],
            f"Abierto en herdr ({reported})",
            author="usuario",
        )
    return result


def wait_session(
    pane_id: str,
    *,
    timeout: float = 10.0,
    interval: float = 0.5,
    sleep: Callable[[float], None] | None = None,
    run: Callable[[list[str]], dict[str, Any]] | None = None,
) -> str | None:
    """Espera a que herdr reporte la sesión del agente recién arrancado."""
    import time

    run = run or _default_runner
    sleep = sleep or time.sleep
    deadline = time.monotonic() + max(0.0, timeout)
    while True:
        for agent in agents(run):
            if str(agent.get("pane_id")) != str(pane_id):
                continue
            info = agent.get("agent_session") or {}
            if info.get("value"):
                return str(info["value"])
        if time.monotonic() >= deadline:
            return None
        sleep(interval)
