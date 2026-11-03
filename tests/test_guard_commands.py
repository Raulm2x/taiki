"""Guard de comandos: publicar, scratch y operaciones git globales."""

from __future__ import annotations

import contextlib
import io
from datetime import datetime, timedelta
from pathlib import Path

import cli
from ticketlib import audit as audit_lib, db, guard, repo, scratch
from tests.support import Harness


def run(h: Harness) -> None:
    conn = h.conn
    tmp = str(h.tmp)
    h.ok(
        not guard.command_is_mutating(
            "cd ~/.config && git push 2>&1 | tail -3"
        )
        and not guard.command_is_mutating(
            "git add -A && git commit -m 'x (T-0001)' && git push"
        )
        and not guard.command_is_mutating('python3 -c "print(1 > 0)"')
        and not guard.command_is_mutating("taiki list --limit 5")
        and not guard.command_is_mutating("git stash list")
        and guard.command_is_mutating("echo hola > nota.txt")
        and guard.command_is_mutating("sed -i 's/a/b/' f")
        and guard.command_is_mutating("git checkout -- .")
        and guard.command_is_mutating("env FOO=1 rm x"),
        "el guard distingue publicar de escribir, con pipes y comillas",
    )
    h.ok(
        guard.command_is_mutating(
            "python3 - <<'PY'\nopen('x', 'w').write('hola')\nPY"
        )
        and not guard.command_is_mutating(
            "python3 - <<'PY'\nprint('a > b')\nPY"
        ),
        "los heredocs solo bloquean cuando el script escribe",
    )

    temp = scratch.create_or_reuse(
        conn, agent="opencode", session_id="ses_scratch"
    )
    h.ok(
        scratch.is_scratch(temp)
        and temp["status"] == "en_progreso"
        and bool(scratch.expires_at(conn, temp["code"])),
        "scratch.create_or_reuse crea un temporal con caducidad",
    )
    again = scratch.create_or_reuse(
        conn, agent="opencode", session_id="ses_scratch"
    )
    h.ok(
        again["code"] == temp["code"],
        "el scratch de la sesión se reutiliza",
    )
    other_scratch = scratch.create_or_reuse(
        conn, agent="opencode", session_id="ses_scratch_otra"
    )
    h.ok(
        other_scratch["code"] != temp["code"],
        "otra sesión recibe su propio scratch",
    )

    topic = repo.create_ticket(
        conn,
        title="Ticket con el tema scratch",
        tags=["scratch"],
    )
    h.ok(
        topic["code"]
        in {row["code"] for row in repo.list_tickets(conn, limit=None)},
        "la etiqueta de tema scratch no oculta el ticket",
    )

    listed = {row["code"] for row in repo.list_tickets(conn, limit=None)}
    h.ok(
        temp["code"] not in listed,
        "el scratch no aparece en list_tickets",
    )
    with_scratch = {
        row["code"]
        for row in repo.list_tickets(
            conn, limit=None, include_scratch=True
        )
    }
    h.ok(
        {temp["code"], other_scratch["code"]} <= with_scratch,
        "include_scratch muestra los temporales",
    )
    metrics = repo.stats(conn)
    h.ok(
        metrics["scratch"] >= 2
        and all(
            row["code"] != temp["code"]
            for row in repo.list_tickets(conn, status="en_progreso", limit=None)
        ),
        "stats cuenta los scratch aparte y no los mezcla",
    )
    stalled_codes = {row["code"] for row in audit_lib.stalled(conn, days=0)}
    h.ok(
        temp["code"] not in stalled_codes,
        "audit no marca un scratch como ticket estancado",
    )
    report = repo.confirm_tickets(conn, [temp["code"]])
    h.ok(
        not report["confirmed"]
        and temp["code"] in report["errors"],
        "confirm rechaza un ticket scratch",
    )

    db.meta_set(
        conn,
        scratch._meta_key(temp["code"]),
        "2000-01-01T00:00:00-05:00",
    )
    conn.commit()
    cleaned = scratch.clean(conn)
    h.ok(
        temp["code"] in cleaned
        and temp["code"]
        not in {row["code"] for row in scratch.list_scratch(conn)},
        "clean archiva los scratch caducados",
    )

    promoted = scratch.promote(conn, other_scratch["code"])
    h.ok(
        not scratch.is_scratch(promoted)
        and promoted["code"]
        in {row["code"] for row in repo.list_tickets(conn, limit=None)},
        "promote devuelve el scratch a la tabla",
    )
    dropped = scratch.create_or_reuse(
        conn, agent="opencode", session_id="ses_scratch_drop"
    )
    scratch.drop(conn, dropped["code"])
    h.ok(
        dropped["code"]
        not in {row["code"] for row in scratch.list_scratch(conn)},
        "drop archiva el scratch sin publicarlo",
    )

    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        code = cli.main(["scratch", "--list"])
    h.ok(
        code == 0 and "scratch" in out.getvalue().lower(),
        "taiki scratch --list responde por CLI",
    )
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        code = cli.main(["list", "--limit", "5"])
    listed_text = out.getvalue()
    h.ok(
        code == 0 and promoted["code"] in listed_text,
        "ticket list muestra el ticket promovido",
    )

    # ── Agentes en paralelo: operaciones git de alcance global ─────────
    h.eq(
        guard.risky_git("git add -A"),
        "git add -A",
        "risky_git detecta git add -A",
    )
    h.ok(
        guard.risky_git("git add src/app.py") is None
        and guard.risky_git("git add -p") is None,
        "risky_git deja pasar el staging por rutas",
    )
    h.ok(
        guard.risky_git("git commit -am 'x'") == "git commit -a"
        and guard.risky_git("git commit -m 'x'") is None,
        "risky_git distingue commit -a de -m",
    )
    h.ok(
        guard.risky_git("cd repo && git stash") == "git stash"
        and guard.risky_git("git reset --soft HEAD~1") == "git reset",
        "risky_git detecta stash y reset",
    )

    parallel_dir = Path.home() / "proyecto-paralelo"
    with h.env(TAIKI_DB=str(Path(tmp) / "paralelo.db")):
        pconn = db.connect()
        try:
            other = repo.create_ticket(
                pconn, title="Trabajo de otra sesión", project=".config"
            )
            repo.claim_ticket(
                pconn, other["code"], agent="opencode", session_id="ses_otra"
            )
            blocked = guard.evaluate(
                tool="bash",
                session="ses_mia",
                agent="opencode",
                command="git add -A",
                cwd=str(parallel_dir),
            )
            h.ok(
                blocked["action"] == "parallel" and not blocked["allow"],
                "git add -A se bloquea si otra sesión tiene un ticket activo",
            )
            scoped = guard.evaluate(
                tool="bash",
                session="ses_mia",
                agent="opencode",
                command="git add docs/a.md",
                cwd=str(parallel_dir),
            )
            h.ok(
                scoped["allow"],
                "stagear una ruta concreta no se bloquea",
            )
            mine = repo.create_ticket(
                pconn, title="Trabajo propio", project=".config"
            )
            repo.claim_ticket(
                pconn, mine["code"], agent="opencode", session_id="ses_mia"
            )
            write_blocked = guard.evaluate(
                tool="write",
                session="ses_mia",
                agent="opencode",
                path=str(parallel_dir / "nota.txt"),
                cwd=str(parallel_dir),
            )
            h.ok(
                write_blocked["action"] == "worktree"
                and not write_blocked["allow"],
                "escribir sin worktree se bloquea con otra sesión activa",
            )
            escape = guard.evaluate(
                tool="write",
                session="ses_mia",
                agent="opencode",
                path=str(parallel_dir / "nota.txt"),
                cwd=str(parallel_dir),
                environ={"TAIKI_SHARED_TREE": "1"},
            )
            h.ok(
                escape["allow"],
                "TAIKI_SHARED_TREE=1 permite el árbol compartido",
            )
            read = guard.evaluate(
                tool="bash",
                session="ses_mia",
                agent="opencode",
                command="git status",
                cwd=str(parallel_dir),
            )
            h.ok(read["allow"], "las lecturas no exigen worktree")
            repo.link_ticket(
                pconn, mine["code"], "worktree", "/tmp/worktree-propio"
            )
            with_tree = guard.evaluate(
                tool="write",
                session="ses_mia",
                agent="opencode",
                path=str(parallel_dir / "nota.txt"),
                cwd=str(parallel_dir),
            )
            h.ok(
                with_tree["allow"] and with_tree["action"] == "active",
                "con worktree enlazado la escritura pasa",
            )
            repo.transition(
                pconn,
                other["code"],
                "cancelado",
                note="fin del escenario",
                actor="usuario",
                actor_type="usuario",
                session_id="ses_otra",
            )
            allowed = guard.evaluate(
                tool="bash",
                session="ses_mia",
                agent="opencode",
                command="git add -A",
                cwd=str(parallel_dir),
            )
            h.ok(
                allowed["allow"],
                "sin otras sesiones activas git add -A sigue permitido",
            )

            # Un claim caducado no debe bloquear a una sesión viva.
            stale = repo.create_ticket(
                pconn, title="Sesión muerta", project=".config"
            )
            repo.claim_ticket(
                pconn, stale["code"], agent="opencode", session_id="ses_muerta"
            )
            past = (
                datetime.now().astimezone() - timedelta(minutes=10)
            ).isoformat(timespec="seconds")
            pconn.execute(
                "UPDATE tickets SET heartbeat_at = ?, lease_until = ? "
                "WHERE code = ?",
                (past, past, stale["code"]),
            )
            pconn.execute(
                "DELETE FROM links WHERE kind IN ('worktree', 'rama') "
                "AND ticket_id = (SELECT id FROM tickets WHERE code = ?)",
                (mine["code"],),
            )
            pconn.commit()
            h.ok(
                any(item["code"] == stale["code"] for item in repo.stale_claims(pconn)),
                "stale_claims detecta el lease vencido del escenario",
            )
            stale_ignored = guard.evaluate(
                tool="write",
                session="ses_mia",
                agent="opencode",
                path=str(parallel_dir / "nota.txt"),
                cwd=str(parallel_dir),
            )
            h.ok(
                stale_ignored["allow"] and stale_ignored["action"] == "active",
                "un claim caducado no exige worktree a la sesión viva",
            )

            # Sin sesión, el ticket propio (sin dueño) no es "otra sesión".
            for code in (mine["code"], stale["code"]):
                repo.transition(
                    pconn,
                    code,
                    "cancelado",
                    note="fin del escenario sin sesión",
                    actor="usuario",
                    actor_type="usuario",
                )
            legacy = repo.create_ticket(
                pconn, title="Ticket sin dueño", project=".config"
            )
            repo.claim_ticket(
                pconn, legacy["code"], agent="opencode", session_id=None
            )
            no_session = guard.evaluate(
                tool="write",
                session=None,
                agent="opencode",
                path=str(parallel_dir / "nota.txt"),
                cwd=str(parallel_dir),
            )
            h.ok(
                no_session["allow"]
                and no_session["action"] == "active"
                and no_session["ticket"] == legacy["code"],
                "el ticket propio sin sesión no se cuenta como otra sesión",
            )
            no_session_git = guard.evaluate(
                tool="bash",
                session=None,
                agent="opencode",
                command="git add -A",
                cwd=str(parallel_dir),
            )
            h.ok(
                no_session_git["allow"],
                "el ticket propio sin sesión no bloquea git add -A",
            )
        finally:
            pconn.close()

    # ── Raíces configuradas y scope de comandos ──────────────────
    project = Path(tmp) / "scope-project"
    (project / "src").mkdir(parents=True)
    (project / "other").mkdir(parents=True)
    extra = Path(tmp) / "etc-simulado"
    extra.mkdir()

    h.ok(
        not guard.path_guarded(project / "src/a.py")
        and guard.path_guarded(project / "src/a.py", [Path.home(), project]),
        "una raíz configurada deja de ser outside",
    )
    h.ok(
        guard.path_guarded("src/a.py", [Path.home(), "."], base=project)
        and not guard.path_guarded(
            "src/a.py", [Path.home(), "otra-carpeta"], base=project
        ),
        "las raíces relativas se resuelven contra el proyecto",
    )
    h.ok(
        not guard.path_guarded(Path("/tmp/opencode/nota"))
        and guard.path_guarded(Path("/tmp/opencode/nota"), ["/tmp"]),
        "una raíz explícita gana a la exención de /tmp",
    )
    h.ok(
        not guard.command_touches_home(
            "rm -rf /tmp/opencode/x", None, [Path.home()]
        )
        and guard.command_touches_home(
            "rm -rf /tmp/opencode/x", None, [Path.home(), "/tmp"]
        )
        and guard.command_touches_home(
            f"rm -rf {project}/src/a.py", None, [Path.home(), project]
        ),
        "command_touches_home entiende las raíces configuradas",
    )

    scopes = ["src/"]
    resources = ["systemd:user/foo.service", "package:jq"]
    h.ok(
        guard.command_scope_problem(
            "sed -i 's/a/b/' src/a.py", scopes, resources, project
        )
        is None
        and guard.command_scope_problem(
            "sed -i 's/a/b/' other/a.py", scopes, resources, project
        )
        == "la ruta 'other/a.py'",
        "sed -i valida el fichero frente al scope",
    )
    h.ok(
        guard.command_scope_problem(
            "echo hola > src/a.py", scopes, resources, project
        )
        is None
        and guard.command_scope_problem(
            "echo hola > /etc/passwd", scopes, resources, project
        )
        == "la redirección a '/etc/passwd'",
        "las redirecciones se validan y echo deja de ser ambiguo",
    )
    h.ok(
        guard.command_scope_problem(
            "tee src/a.py", scopes, resources, project
        )
        is None
        and guard.command_scope_problem(
            "tee /etc/hosts", scopes, resources, project
        )
        == "la ruta '/etc/hosts'",
        "tee valida todos sus destinos",
    )
    h.ok(
        guard.command_scope_problem(
            "cp a.py src/b.py", scopes, resources, project
        )
        is None
        and guard.command_scope_problem(
            "cp src/a.py /etc/x", scopes, resources, project
        )
        == "la ruta '/etc/x'"
        and guard.command_scope_problem(
            "install a.py src/b.py", scopes, resources, project
        )
        is None
        and guard.command_scope_problem(
            "mv src/a.py /etc/x", scopes, resources, project
        )
        == "la ruta '/etc/x'",
        "cp, install y mv validan el destino escrito",
    )
    h.ok(
        guard.command_scope_problem(
            "python3 -c \"open('src/a.py', 'w')\"", scopes, resources, project
        )
        is not None
        and "python3" in guard.command_scope_problem(
            "python3 -c \"open('src/a.py', 'w')\"", scopes, resources, project
        ),
        "python -c es ambiguo y se bloquea en scope",
    )
    h.ok(
        guard.command_scope_problem(
            "systemctl --user edit foo.service", scopes, resources, project
        )
        is None
        and guard.command_scope_problem(
            "systemctl --user edit bar.service", scopes, resources, project
        )
        == "el recurso 'systemd:user/bar.service'"
        and guard.command_scope_problem(
            "pacman -S jq", scopes, resources, project
        )
        is None
        and guard.command_scope_problem(
            "pacman -S vim", scopes, resources, project
        )
        == "el recurso 'package:vim'",
        "los recursos tipados cubren systemctl y pacman",
    )
    h.ok(
        guard.command_scope_problem(
            "git add src/a.py", scopes, resources, project
        )
        is None,
        "git se delega a la capa de commits",
    )
