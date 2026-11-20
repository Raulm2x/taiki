"""Guard de comandos: publicar, scratch y operaciones git globales."""

from __future__ import annotations

import contextlib
import io
import json
from datetime import datetime, timedelta
from pathlib import Path

import cli
from commands.guard import _print_perms as print_perms
from ticketlib import audit as audit_lib, db, guard, repo, scratch
from ticketlib.guard import commands as shell_rules
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

    # ── Bypasses de prefijos y sed --in-place ────────────────────
    h.ok(
        guard.command_is_mutating("sudo -u root rm -rf /x")
        and guard.command_is_mutating("env -i rm x")
        and guard.command_is_mutating("time rm x")
        and guard.command_is_mutating("timeout 5 rm x")
        and guard.command_is_mutating("sed --in-place 's/a/b/' f")
        and guard.command_is_mutating("sed --in-place=.bak 's/a/b/' f"),
        "los prefijos y sed --in-place no ocultan el comando real",
    )
    h.ok(
        guard.command_scope_problem(
            "sudo -u root rm other/a.py", scopes, resources, project
        )
        == "la ruta 'other/a.py'",
        "sudo con opciones valida el destino real",
    )
    h.ok(
        guard.command_scope_problem(
            "python3 -c \"print(1)\"", scopes, resources, project
        )
        is not None
        and guard.command_scope_problem(
            "python3 -m pytest", scopes, resources, project
        )
        is None,
        "scope bloquea python -c por defecto pero no python -m",
    )

    # ── -t/--target-directory y enlaces ──────────────────────────
    h.ok(
        guard.command_scope_problem(
            "cp -t /etc src/a.py", scopes, resources, project
        )
        == "la ruta '/etc'"
        and guard.command_scope_problem(
            "mv -t /etc src/a.py", scopes, resources, project
        )
        == "la ruta '/etc'"
        and guard.command_scope_problem(
            "install -t /etc src/a.py", scopes, resources, project
        )
        == "la ruta '/etc'",
        "cp, mv e install validan -t DIR",
    )
    h.ok(
        guard.command_scope_problem(
            "cp --target-directory=/etc src/a.py", scopes, resources, project
        )
        == "la ruta '/etc'"
        and guard.command_scope_problem(
            "ln src/a.py /etc/x", scopes, resources, project
        )
        == "la ruta '/etc/x'"
        and guard.command_scope_problem(
            "ln -t /etc src/a.py", scopes, resources, project
        )
        == "la ruta '/etc'",
        "ln y --target-directory validan el destino",
    )
    h.ok(
        guard.command_scope_problem(
            "cp -t src other/a.py", scopes, resources, project
        )
        is None
        and guard.command_scope_problem(
            "ln src/a.py src/b.py", scopes, resources, project
        )
        is None,
        "los destinos dentro del scope no se bloquean",
    )

    # ── git que escribe el árbol vivo en scope ──────────────────
    h.ok(
        all(
            problem is not None and sub in problem
            for sub, problem in (
                (
                    "git checkout",
                    guard.command_scope_problem(
                        "git checkout -- docs/x", scopes, resources, project
                    ),
                ),
                (
                    "git restore",
                    guard.command_scope_problem(
                        "git restore docs/x", scopes, resources, project
                    ),
                ),
                (
                    "git apply",
                    guard.command_scope_problem(
                        "git apply fuera.patch", scopes, resources, project
                    ),
                ),
                (
                    "git clean",
                    guard.command_scope_problem(
                        "git clean -fd", scopes, resources, project
                    ),
                ),
            )
        )
        and guard.command_scope_problem("git status", scopes, resources, project)
        is None,
        "scope bloquea git que escribe el árbol y deja pasar la lectura",
    )

    # ── Redirecciones con fd y falsos positivos ─────────────────
    h.ok(
        guard.command_scope_problem(
            "echo hola 1> other/a", scopes, resources, project
        )
        == "la redirección a 'other/a'"
        and guard.command_scope_problem(
            "echo hola 2> other/a", scopes, resources, project
        )
        == "la redirección a 'other/a'"
        and guard.command_scope_problem(
            "echo hola 1> src/a", scopes, resources, project
        )
        is None,
        "las redirecciones 1> y 2> validan su destino",
    )
    h.ok(
        guard.command_scope_problem(
            "echo hola 2>&1", scopes, resources, project
        )
        is None
        and guard.command_scope_problem(
            "echo hola >&2", scopes, resources, project
        )
        is None,
        "las duplicaciones de fd no se confunden con ficheros",
    )
    h.ok(
        guard.command_scope_problem(
            "chmod 644 src/a.py", scopes, resources, project
        )
        is None
        and guard.command_scope_problem(
            "chmod +x src/a.py", scopes, resources, project
        )
        is None
        and guard.command_scope_problem(
            "chown u:g src/a.py", scopes, resources, project
        )
        is None
        and guard.command_scope_problem(
            "truncate -s 0 src/a.py", scopes, resources, project
        )
        is None,
        "modo, tamaño y propietario no se confunden con rutas",
    )
    h.ok(
        guard.command_scope_problem(
            "dd if=/tmp/entrada of=src/a.py", scopes, resources, project
        )
        is None
        and guard.command_scope_problem(
            "dd if=/tmp/entrada of=/etc/x", scopes, resources, project
        )
        == "la ruta '/etc/x'",
        "dd valida solo el destino of=",
    )

    # ── Allowlist documentada: perms, deny explicativo ───────────
    agents_dir = Path(__file__).resolve().parent.parent / ".opencode" / "agents"
    profiles = shell_rules.profile_rules(agents_dir)
    h.ok(
        bool(profiles)
        and all(item["name"].startswith("worker") for item in profiles),
        "profile_rules lee los perfiles worker de .opencode/agents",
    )
    worker = next(
        (item for item in profiles if item["name"] == "worker"), None
    )
    rules = worker["rules"] if worker else []
    h.ok(
        any(
            rule.get("action") == "shell"
            and rule.get("resource") == "git add -A*"
            and rule.get("effect") == "deny"
            for rule in rules
        ),
        "perms encuentra el deny conocido de git add -A en worker",
    )
    dup = shell_rules.parse_permissions(
        "permissions:\n"
        "  - action: shell\n"
        '    resource: "git add *"\n'
        "    effect: allow\n"
        "  - action: shell\n"
        '    resource: "git add *"\n'
        "    effect: deny\n"
        "---\n"
    )
    h.ok(
        len(dup) == 2
        and not shell_rules.rule_wins(dup, 0)
        and shell_rules.rule_wins(dup, 1),
        "la última regla que casa es la que gana",
    )
    verdict = shell_rules.command_verdict("git add src/a.py && cat nota", rules)
    h.ok(
        verdict["allowed"]
        and all(seg["effect"] == "allow" for seg in verdict["segments"]),
        "el veredicto permite el staging por rutas del perfil worker",
    )
    denied_verdict = shell_rules.command_verdict("git add .", rules)
    h.ok(
        not denied_verdict["allowed"]
        and denied_verdict["segments"][0]["effect"] == "deny",
        "el perfil worker deniega git add .",
    )
    # T-0088.01: lecturas de git que faltaban en la allowlist, en los cuatro
    # perfiles worker. El deny de merge quedó afinado a `git merge *` para que
    # no tape `git merge-base`: sin ese afinado, la última regla que casa
    # vuelve a denegar la lectura.
    git_reads = (
        "git merge-base --is-ancestor fb5bf71 HEAD",
        "git -C .worktrees/T-0001 merge-base --is-ancestor fb5bf71 HEAD",
        "git rev-list --count fb5bf71..HEAD",
        "git cat-file -t v0.4.1",
        "git branch --contains fb5bf71",
        "git for-each-ref refs/heads/agent",
    )
    denied_reads = sorted(
        f"{profile['name']}: {cmd}"
        for cmd in git_reads
        for profile in profiles
        if not shell_rules.command_verdict(cmd, profile["rules"])[
            "allowed"
        ]
    )
    h.ok(
        not denied_reads,
        "los cuatro perfiles worker permiten las lecturas de git "
        "nuevas (merge-base, rev-list, cat-file, branch --contains, "
        f"for-each-ref): {'; '.join(denied_reads) if denied_reads else 'ok'}",
    )
    h.ok(
        all(
            not shell_rules.command_verdict(cmd, profile["rules"])[
                "allowed"
            ]
            for profile in profiles
            for cmd in (
                "git merge develop",
                "git merge --no-ff develop",
                "git push origin develop",
                "rm -rf /tmp/x",
                "sudo id",
            )
        ),
        "ninguna regla nueva abre escritura: merge, push, rm -rf y sudo "
        "siguen denegados en todos los perfiles worker",
    )
    merge_rule = next(
        (
            index
            for index, rule in enumerate(rules)
            if rule.get("action") == "shell"
            and rule.get("resource") == "git merge-base *"
            and rule.get("effect") == "allow"
        ),
        None,
    )
    h.ok(
        merge_rule is not None
        and shell_rules.rule_wins(rules, merge_rule)
        and not shell_rules.command_is_mutating(
            "git rev-list --count fb5bf71..HEAD"
        )
        and not shell_rules.command_is_mutating("git cat-file -t v0.4.1")
        and not shell_rules.command_is_mutating(
            "git for-each-ref refs/heads/agent"
        )
        and shell_rules.risky_git("git merge-base --is-ancestor a HEAD")
        is None,
        "el allow de merge-base no queda tapado y el guard trata las "
        "lecturas nuevas como lecturas",
    )
    h.ok(
        "git add --" in (shell_rules.deny_alternative("git add -A") or "")
        and "commitea el WIP"
        in (shell_rules.deny_alternative("git stash") or "")
        and shell_rules.deny_alternative("desconocido") is None,
        "deny_alternative sugiere la alternativa conocida y calla lo demás",
    )
    # Test adverso: recorre el mapa completo y evalúa cada muestra con la
    # allowlist real de cada perfil worker. Ninguna alternativa puede estar
    # denegada (la trampa original: sugerir `git checkout -- <ruta>` cuando
    # el perfil deniega `git checkout -- *`).
    for label in shell_rules.deny_alternative_labels():
        text = shell_rules.deny_alternative(label) or ""
        samples = shell_rules.deny_alternative_samples(label)
        denied = sorted(
            f"{profile['name']}: {sample}"
            for sample in samples
            for profile in profiles
            if not shell_rules.command_verdict(sample, profile["rules"])[
                "allowed"
            ]
        )
        h.ok(
            (bool(samples) or "conductor" in text) and not denied,
            f"la alternativa de {label} es ejecutable por todo worker: "
            f"{'; '.join(denied) if denied else text}",
        )
    h.ok(
        shell_rules.high_risk_command("sudo id") == "sudo"
        and shell_rules.high_risk_command("rm -f /etc/x") == "sistema"
        and shell_rules.command_is_mutating("rm -rf /x")
        and shell_rules.risky_git("git add .") == "git add -A",
        "los denies destructivos (sudo, /etc, rm -rf, staging masivo) siguen",
    )
    h.ok(
        shell_rules.command_is_mutating("find . -name x -delete")
        and shell_rules.command_is_mutating("find -delete .")
        and shell_rules.command_is_mutating("echo hi 2> log")
        and shell_rules.command_is_mutating("echo hi >log"),
        "flags y redirecciones se deniegan en ambas posiciones",
    )

    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        code = cli.main(["perms", "--json"])
    payload = json.loads(out.getvalue())
    h.ok(
        code == 0
        and any(
            rule.get("resource") == "git add -A*"
            and rule.get("effect") == "deny"
            for profile in payload["profiles"]
            for rule in profile["rules"]
        )
        and "git add -A" in payload["guard"]["git_global"],
        "taiki perms --json lista una regla conocida por CLI",
    )
    h.ok(
        shell_rules.parse_permissions("permissions: {}") == [],
        "parse_permissions no inventa reglas de un permissions: {} inline",
    )
    empty_summary = {
        "sin_ticket": [],
        "git_sin_ticket": [],
        "git_global": [],
        "alto_riesgo": [],
    }
    out_perm = io.StringIO()
    with contextlib.redirect_stdout(out_perm):
        print_perms(
            [
                {
                    "name": "worker-fantasma",
                    "origin": "inline",
                    "rules": [],
                },
                {"name": "worker-dup", "origin": "inline", "rules": dup},
            ],
            empty_summary,
        )
    perm_text = out_perm.getvalue()
    h.ok(
        "sin reglas shell" in perm_text
        and "tapada" in perm_text
        and "gana" not in perm_text,
        "perms avisa del perfil sin reglas y marca la solapada como tapada",
    )
    h.ok(
        all(
            "covered" in rule and "wins" not in rule
            for profile in payload["profiles"]
            for rule in profile["rules"]
        )
        and all("shell_rules" in profile for profile in payload["profiles"]),
        "perms --json expone covered y shell_rules por perfil",
    )
    out_cmd = io.StringIO()
    with contextlib.redirect_stdout(out_cmd):
        code_cmd = cli.main(["perms", "--command", "git add .", "--json"])
    payload_cmd = json.loads(out_cmd.getvalue())
    h.ok(
        code_cmd == 3
        and not payload_cmd["allowed"]
        and "git add --" in json.dumps(payload_cmd["guard"]),
        "taiki perms --command deniega git add . con su alternativa",
    )
    out_cfg = io.StringIO()
    with contextlib.redirect_stdout(out_cfg):
        code_cfg = cli.main(["config", "show"])
    h.ok(
        code_cfg == 0 and isinstance(json.loads(out_cfg.getvalue()), dict),
        "taiki config show imprime el config.json resuelto",
    )

    with h.env(TAIKI_DB=str(Path(tmp) / "perms-deny.db")):
        dconn = db.connect()
        try:
            other = repo.create_ticket(
                dconn, title="Otra sesión viva", project=".config"
            )
            repo.claim_ticket(
                dconn, other["code"], agent="opencode", session_id="ses_otra"
            )
            denied = guard.evaluate(
                tool="bash",
                session="ses_mia",
                agent="opencode",
                command="git add .",
                cwd=str(Path.home() / "proyecto-deny"),
            )
            h.ok(
                not denied["allow"]
                and denied["action"] == "parallel"
                and "git add --" in denied["message"],
                "el deny de git add . sugiere git add -- <ruta>",
            )
        finally:
            dconn.close()
