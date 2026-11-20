"""Reparación de enlaces de tickets: unlink y links_repair (T-0085.06)."""

from __future__ import annotations

import subprocess
from pathlib import Path

from mcp_layer import handlers
from ticketlib import db, repo
from ticketlib.domain import NotFoundError
from ticketlib.store import links as links_store
from tests.seeds import git_repo
from tests.support import Harness


def _commit(path: Path, message: str = "base") -> str:
    """Commit vacío en el repo temporal; devuelve el hash completo."""
    subprocess.run(
        ["git", "-C", str(path), "commit", "-q", "--allow-empty", "-m", message],
        check=True,
    )
    return subprocess.run(
        ["git", "-C", str(path), "rev-parse", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def _raw_link(conn, ticket_id: int, kind: str, ref: str) -> None:
    """Enlace insertado a mano: simula lo que una reorg deja colgando."""
    with db.tx(conn):
        conn.execute(
            "INSERT INTO links(ticket_id, kind, ref, created_at) "
            "VALUES (?, ?, ?, datetime('now'))",
            (ticket_id, kind, ref),
        )


def _links_left(conn, ticket_id: int) -> list[str]:
    return [
        f"{row['kind']}:{row['ref']}"
        for row in conn.execute(
            "SELECT kind, ref FROM links WHERE ticket_id = ?", (ticket_id,)
        ).fetchall()
    ]


def _desvinculados(conn, ticket_id: int) -> int:
    return conn.execute(
        "SELECT COUNT(*) AS n FROM events WHERE ticket_id = ? "
        "AND action = 'desvincular'",
        (ticket_id,),
    ).fetchone()["n"]


def _unlink(h: Harness) -> None:
    """unlink quita el enlace propio y rechaza el ajeno o inexistente."""
    conn = h.conn
    holder = repo.create_ticket(conn, title="Portador", kind="mantenimiento")
    other = repo.create_ticket(conn, title="Ajeno", kind="mantenimiento")
    repo.link_ticket(conn, holder["code"], "otro", "T-0001", actor="tester")
    after = links_store.unlink_ticket(
        conn,
        holder["code"],
        "otro",
        "T-0001",
        actor="tester",
        reason="prueba de borrado",
    )
    h.ok(
        "otro:T-0001" not in _links_left(conn, holder["id"])
        and _desvinculados(conn, holder["id"]) == 1
        and after["code"] == holder["code"],
        "unlink quita el enlace propio y registra el evento desvincular",
    )
    h.raises(
        NotFoundError,
        lambda: links_store.unlink_ticket(
            conn, other["code"], "otro", "T-0001", actor="tester"
        ),
        "unlink de un enlace ajeno al ticket falla",
    )
    h.raises(
        NotFoundError,
        lambda: links_store.unlink_ticket(
            conn, holder["code"], "otro", "T-9999", actor="tester"
        ),
        "unlink de un enlace que ya no existe falla",
    )
    h.eq(
        _desvinculados(conn, other["id"]),
        0,
        "los intentos fallidos de unlink no escriben eventos",
    )


def _repair(h: Harness) -> None:
    """Dry-run detecta sin escribir; apply retira y protege lo bloqueado."""
    conn = h.conn
    git = git_repo(h.tmp / "repo")
    sha = _commit(git)
    folder = h.tmp / "carpeta"
    folder.mkdir()
    vivo = folder / "vivo.md"
    vivo.write_text("vivo", encoding="utf-8")

    sano = repo.create_ticket(conn, title="Sano", kind="mantenimiento")
    roto = repo.create_ticket(conn, title="Roto", kind="mantenimiento")
    blocked = repo.create_ticket(conn, title="Bloqueado", kind="mantenimiento")
    repo.transition(conn, blocked["code"], "pendiente", actor="tester")
    repo.transition(conn, blocked["code"], "bloqueado", actor="tester")

    repo.link_ticket(conn, sano["code"], "commit", sha, actor="tester")
    repo.link_ticket(conn, sano["code"], "archivo", str(vivo), actor="tester")
    repo.link_ticket(conn, sano["code"], "url", "https://ejemplo", actor="tester")
    _raw_link(conn, roto["id"], "commit", "0" * 40)
    _raw_link(conn, roto["id"], "archivo", str(folder / "borrado.md"))
    _raw_link(conn, roto["id"], "depende", "T-9999")
    protected = str(h.tmp / "sin-dir" / "f.md")
    _raw_link(conn, roto["id"], "archivo", protected)
    _raw_link(conn, blocked["id"], "depende", "T-9998")

    dry = links_store.repair_links(conn, root=h.tmp, repos=[git])
    h.ok(
        dry["dry_run"] is True
        and dry["checked"] == 7
        and len(dry["broken"]) == 4
        and dry["retired"] == []
        and all(item["code"] != sano["code"] for item in dry["broken"])
        and not any(item["ref"].endswith("f.md") for item in dry["broken"]),
        "el dry-run detecta commits, depende y archivo rotos sin tocar los "
        "válidos ni decidir sobre un padre ilegible o ausente",
    )
    blocked_item = next(
        item for item in dry["broken"] if item["ref"] == "T-9998"
    )
    h.ok(
        blocked_item["retireable"] is False,
        "un depende de un ticket bloqueado no es retirable sin force",
    )
    h.ok(
        len(_links_left(conn, roto["id"])) == 4
        and len(_links_left(conn, sano["id"])) == 3,
        "el dry-run no escribe: los enlaces siguen igual",
    )

    applied = links_store.repair_links(
        conn, root=h.tmp, repos=[git], apply=True, actor="tester"
    )
    h.ok(
        applied["dry_run"] is False
        and len(applied["retired"]) == 3
        and all(item["ref"] != "T-9998" for item in applied["retired"])
        and _links_left(conn, roto["id"]) == [f"archivo:{protected}"]
        and _desvinculados(conn, roto["id"]) == 3
        and _desvinculados(conn, blocked["id"]) == 0,
        "apply retira los rotos con evento y motivo, y conserva los del "
        "ticket bloqueado sin force",
    )
    h.ok(
        conn.execute(
            "SELECT COUNT(*) AS n FROM tickets"
        ).fetchone()["n"] >= 3
        and conn.execute(
            "SELECT 1 FROM tickets WHERE id = ?", (roto["id"],)
        ).fetchone()
        is not None,
        "apply nunca borra filas de tickets, solo enlaces",
    )

    forced = links_store.repair_links(
        conn, root=h.tmp, repos=[git], apply=True, force=True, actor="tester"
    )
    h.ok(
        [item["ref"] for item in forced["retired"]] == ["T-9998"]
        and _links_left(conn, blocked["id"]) == []
        and _desvinculados(conn, blocked["id"]) == 1
        and sorted(_links_left(conn, sano["id"]))
        == sorted([f"commit:{sha}", f"archivo:{vivo}", "url:https://ejemplo"]),
        "force retira también el depende del ticket bloqueado y los enlaces "
        "válidos sobreviven a todo el ciclo",
    )


def _contract(h: Harness) -> None:
    """Contrato MCP: recibo de unlink y forma del informe de links_repair."""
    conn = h.conn
    ticket = repo.create_ticket(conn, title="Contrato", kind="mantenimiento")
    repo.link_ticket(conn, ticket["code"], "otro", "enlace", actor="tester")
    receipt = handlers._handle_unlink(
        conn,
        {"code": ticket["code"], "kind": "otro", "ref": "enlace"},
        session="ses-test",
        actor="tester",
    )
    h.ok(
        receipt.get("unlinked") == {"kind": "otro", "ref": "enlace"}
        and receipt.get("code") == ticket["code"],
        "el handler unlink devuelve un recibo con el enlace quitado",
    )
    report = handlers._handle_links_repair(
        conn, {"apply": False, "root": str(h.tmp)}, actor="tester"
    )
    h.ok(
        set(report) == {"checked", "broken", "retired", "dry_run"}
        and report["dry_run"] is True
        and report["retired"] == [],
        "links_repair sin apply devuelve {checked, broken, retired, dry_run}",
    )


def run(h: Harness) -> None:
    _unlink(h)
    _repair(h)
    _contract(h)
