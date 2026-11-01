"""Comentarios y enlaces de tickets."""

from __future__ import annotations

import re
import sqlite3
from typing import Any

from .. import db
from ..domain import LINK_KINDS, NotFoundError, ValidationError
from ..gitutil import resolve_commit
from ..util import check_enum, normalize_agent, now

TICKET_CODE_RE = re.compile(r"\bT-\d{4,}(?:\.\d+)?\b")
from .common import event, row_by_code
from .tickets import get_ticket


def comment_ticket(
    conn: sqlite3.Connection,
    code: Any,
    body: str,
    author: str = "agente",
    actor_type: str = "agente",
    session_id: str | None = None,
) -> dict[str, Any]:
    body = (body or "").strip()
    author = normalize_agent(author)
    if not body:
        raise ValidationError("El comentario no puede estar vacío")
    row = row_by_code(conn, code)
    with db.tx(conn):
        conn.execute(
            "INSERT INTO comments(ticket_id, ts, author, body) VALUES (?, ?, ?, ?)",
            (row["id"], now(), author, body),
        )
        event(
            conn,
            row["id"],
            "comentar",
            author,
            actor_type,
            session_id,
            row["status"],
            row["status"],
            "Comentario añadido",
        )
    return get_ticket(conn, code)


def link_ticket(
    conn: sqlite3.Connection,
    code: Any,
    kind: str,
    ref: str,
    actor: str = "agente",
    actor_type: str = "agente",
    session_id: str | None = None,
) -> dict[str, Any]:
    check_enum(kind, LINK_KINDS, "tipo de enlace")
    ref = (ref or "").strip()
    if not ref:
        raise ValidationError("La referencia del enlace es obligatoria")
    if kind == "commit":
        full = resolve_commit(ref)
        if full:
            ref = full
    row = row_by_code(conn, code)
    with db.tx(conn):
        if kind == "commit":
            for existing in conn.execute(
                "SELECT id, ref FROM links WHERE ticket_id = ? AND kind = 'commit'",
                (row["id"],),
            ).fetchall():
                old = existing["ref"]
                if old == ref or old.startswith(ref):
                    return get_ticket(conn, code)
                if ref.startswith(old):
                    conn.execute(
                        "UPDATE links SET ref = ? WHERE id = ?",
                        (ref, existing["id"]),
                    )
                    return get_ticket(conn, code)
        cur = conn.execute(
            "INSERT OR IGNORE INTO links(ticket_id, kind, ref, created_at) "
            "VALUES (?, ?, ?, ?)",
            (row["id"], kind, ref, now()),
        )
        if cur.rowcount:
            event(
                conn,
                row["id"],
                "enlazar",
                actor,
                actor_type,
                session_id,
                row["status"],
                row["status"],
                f"{kind}: {ref}",
            )
    return get_ticket(conn, code)


def auto_link_commit(
    conn: sqlite3.Connection,
    message: str,
    commit: str,
    actor: str = "agente",
    actor_type: str = "agente",
    session_id: str | None = None,
) -> dict[str, Any]:
    """Enlaza un commit a cada ticket mencionado en el mensaje.

    Los códigos ausentes no abortan el resto: se devuelven en ``missing``
    para que el llamante pueda avisar sin fallar el hook de git.
    """
    commit = (commit or "").strip()
    if not commit:
        raise ValidationError("La referencia del commit es obligatoria")

    codes = list(dict.fromkeys(TICKET_CODE_RE.findall(message or "")))
    linked: list[str] = []
    missing: list[str] = []
    for code in codes:
        try:
            link_ticket(
                conn,
                code,
                "commit",
                commit,
                actor=actor,
                actor_type=actor_type,
                session_id=session_id,
            )
        except NotFoundError:
            missing.append(code)
        else:
            linked.append(code)

    return {"commit": commit, "linked": linked, "missing": missing}
