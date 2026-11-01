"""commit-msg, pre-push y auditoría."""

from __future__ import annotations

import os
from pathlib import Path
from ticketlib import audit as audit_lib, githook, repo
from tests.seeds import git_repo, repo_with_hooks
from tests.support import Harness


def run(h: Harness) -> None:
    conn = h.conn
    tmp = str(h.tmp)
    hook_repo = repo_with_hooks(Path(tmp) / "githook_repo")

    # ── commit-msg y auditoría ───────────────────────────────────
    os.environ.pop("TICKETS_HOOK", None)
    approved = repo.create_ticket(conn, title="Aprobado para commit")
    repo.claim_ticket(conn, approved["code"], agent="test")
    repo.complete_ticket(
        conn, approved["code"], resolution="listo para revisión"
    )
    h.ok(
        repo.get_ticket(conn, approved["code"])["iterations"] == 1,
        "completar cuenta la primera iteración",
    )
    repo.transition(
        conn,
        approved["code"],
        "en_progreso",
        note="revisión: falta detalle",
        actor="usuario",
        actor_type="usuario",
    )
    repo.complete_ticket(
        conn, approved["code"], resolution="segunda entrega"
    )
    h.ok(
        repo.get_ticket(conn, approved["code"])["iterations"] == 2,
        "rechazar y volver a completar cuenta otra iteración",
    )
    repo.confirm_tickets(conn, [approved["code"]])
    h.ok(
        not githook.check_commit_message(conn, "arreglo sin ticket")["ok"],
        "commit-msg rechaza mensajes sin ticket",
    )
    h.ok(
        githook.check_commit_message(
            conn, f"arreglo {approved['code']}"
        )["ok"],
        "commit-msg acepta un ticket confirmado",
    )
    pending_commit = repo.create_ticket(conn, title="Sin confirmar")
    repo.claim_ticket(conn, pending_commit["code"], agent="test")
    repo.complete_ticket(
        conn, pending_commit["code"], resolution="pendiente de revisión"
    )
    body_ok = githook.check_commit_message(
        conn,
        f"arreglo {approved['code']}\n\nrepasa {pending_commit['code']}",
    )
    h.ok(
        body_ok["ok"] and body_ok["warnings"] == [],
        "el cuerpo no avisa de códigos que sí existen",
    )
    allowed_commit = githook.check_commit_message(
        conn, f"arreglo {pending_commit['code']}"
    )
    h.ok(
        allowed_commit["ok"],
        "commit-msg permite un ticket completado pendiente de confirmar",
    )
    draft = repo.create_ticket(conn, title="Sin reclamar")
    blocked_draft = githook.check_commit_message(
        conn, f"arreglo {draft['code']}"
    )
    h.ok(
        not blocked_draft["ok"]
        and blocked_draft["unconfirmed"] == [draft["code"]],
        "commit-msg bloquea un ticket nuevo sin reclamar",
    )
    h.ok(
        githook.check_commit_message(conn, "x T-9999")["missing"]
        == ["T-9999"],
        "commit-msg detecta códigos inexistentes",
    )
    h.ok(
        githook.check_commit_message(conn, "x [sin-ticket]")["skipped"]
        == "bypass",
        "commit-msg respeta el bypass",
    )
    mixed = githook.check_commit_message(
        conn,
        f"arreglo {approved['code']}\n\nExplica T-8888 y T-7777.",
    )
    h.ok(
        mixed["ok"]
        and len(mixed["warnings"]) == 2
        and mixed["missing"] == [],
        "el cuerpo con códigos inexistentes solo avisa",
    )
    wrong_subject = githook.check_commit_message(
        conn,
        f"arreglo T-8888\n\nfinal {approved['code']}",
    )
    h.ok(
        not wrong_subject["ok"]
        and wrong_subject["missing"] == ["T-8888"],
        "el asunto manda aunque el cuerpo tenga un ticket válido",
    )
    os.environ["TAIKI_HOOK"] = "off"
    h.ok(
        githook.check_commit_message(conn, "sin ticket")["skipped"]
        == "env",
        "TAIKI_HOOK=off desactiva commit-msg",
    )
    os.environ.pop("TAIKI_HOOK", None)
    os.environ["TICKETS_HOOK"] = "off"
    h.ok(
        githook.check_commit_message(conn, "sin ticket")["skipped"]
        == "env",
        "TICKETS_HOOK=off desactiva commit-msg",
    )
    os.environ.pop("TICKETS_HOOK", None)

    def fake_push_git(*args, cwd=None):
        if args[0] == "rev-list":
            return "abc1234"
        if args[0] == "show":
            return f"arreglo {pending_commit['code']}"
        return ""

    push = githook.check_push(
        conn,
        repo=str(hook_repo),
        refs_text="refs/heads/main abc1234 refs/heads/main 0000",
        git_func=fake_push_git,
    )
    h.ok(
        not push["ok"]
        and push["blocked"][0]["unconfirmed"]
        == [pending_commit["code"]],
        "pre-push bloquea tickets sin confirmar",
    )

    def fake_push_ok(*args, cwd=None):
        if args[0] == "rev-list":
            return "abc1234"
        if args[0] == "show":
            return f"arreglo {approved['code']}"
        return ""

    h.ok(
        githook.check_push(
            conn,
            repo=str(hook_repo),
            refs_text="refs/heads/main abc1234 refs/heads/main 0000",
            git_func=fake_push_ok,
        )["ok"],
        "pre-push deja pasar tickets confirmados",
    )
    os.environ.pop("TICKETS_HOOK", None)

    pending_audit = repo.create_ticket(conn, title="Cerrado sin resolución")
    conn.execute(
        "UPDATE tickets SET status = 'completado', resolution = '' "
        "WHERE code = ?",
        (pending_audit["code"],),
    )
    conn.commit()
    data = audit_lib.audit(conn, repo=str(hook_repo), days=1)
    h.ok(
        any(item["code"] == pending_audit["code"] for item in data["unresolved"]),
        "audit detecta cerrados sin resolución",
    )
    h.ok(
        data["hooks"]
        == {
            "post-commit": True,
            "commit-msg": True,
            "pre-push": True,
            "pre-commit": True,
        },
        "audit informa de los cuatro hooks del repo",
    )

    # ── Migración de hooks históricos ticket -> taiki ──────────────────
    legacy_repo = git_repo(Path(tmp) / "legacy_repo")
    legacy_hook = legacy_repo / ".git" / "hooks" / "post-commit"
    legacy_hook.write_text(
        "#!/bin/sh\n"
        "# >>> tickets auto-link >>>\n"
        "if command -v ticket >/dev/null 2>&1; then\n"
        "    ticket auto-link --quiet || true\n"
        "fi\n"
        "# <<< tickets auto-link <<<\n",
        encoding="utf-8",
    )
    githook.install_post_commit_hook(legacy_repo)
    migrated = legacy_hook.read_text(encoding="utf-8")
    h.ok(
        "taiki auto-link" in migrated and "tickets auto-link" not in migrated,
        "hook-install migra el bloque histórico a taiki",
    )
    githook.install_post_commit_hook(legacy_repo)
    h.eq(
        legacy_hook.read_text(encoding="utf-8").count("taiki auto-link"),
        migrated.count("taiki auto-link"),
        "reinstalar el hook migrado no duplica el bloque",
    )
