"""Comentarios y enlaces de tickets."""

from __future__ import annotations

import re
import sqlite3
from typing import Any

from .. import db
from ..domain import (
    DEPENDENCY_KIND,
    LINK_KINDS,
    NotFoundError,
    ValidationError,
)
from ..gitutil import resolve_commit
from ..util import check_enum, normalize_agent, now

TICKET_CODE_RE = re.compile(r"\bT-\d{4,}(?:\.\d+)?\b")
from .common import event, row_by_code
from .queries import get_ticket


def dependency_reaches(
    conn: sqlite3.Connection, start: str, target: str
) -> bool:
    """¿``start`` depende, directa o transitivamente, de ``target``?"""
    pending = [start]
    seen: set[str] = set()
    while pending:
        code = pending.pop()
        if code == target:
            return True
        if code in seen:
            continue
        seen.add(code)
        pending.extend(
            row["ref"]
            for row in conn.execute(
                "SELECT l.ref FROM links l JOIN tickets t ON t.id = l.ticket_id "
                "WHERE t.code = ? AND l.kind = ?",
                (code, DEPENDENCY_KIND),
            )
        )
    return False


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
    *,
    resolve: bool = True,
) -> dict[str, Any]:
    """Enlaza una referencia a un ticket.

    Con ``kind="commit"`` se resuelve el hash completo **antes** de abrir la
    transacción. Los llamantes que ya lo resolvieron pasan ``resolve=False``
    para no ejecutar git con el lock de escritura tomado.

    ``kind="external"`` enlaza un commit de **otro** repositorio (por ejemplo,
    dotfiles) sin resolverlo en el repo del ticket. El formato de ``ref`` es
    ``repo@sha``: el nombre o etiqueta del repositorio, ``@`` y el hash corto
    o completo. ``taiki commit --repo <ruta>`` lo crea automáticamente y
    ``taiki link <código> external <repo@sha>`` permite registrarlo a mano.
    """
    check_enum(kind, LINK_KINDS, "tipo de enlace")
    ref = (ref or "").strip()
    if not ref:
        raise ValidationError("La referencia del enlace es obligatoria")
    if kind == "commit" and resolve:
        full = resolve_commit(ref)
        if full:
            ref = full
    row = row_by_code(conn, code)
    if kind == DEPENDENCY_KIND:
        # Una dependencia es siempre otro ticket: se normaliza el código y se
        # rechazan los ciclos antes de tocar la tabla.
        target = row_by_code(conn, ref)
        ref = target["code"]
        if target["id"] == row["id"]:
            raise ValidationError("Un ticket no puede depender de sí mismo")
        if dependency_reaches(conn, ref, row["code"]):
            raise ValidationError(
                f"Dependencia circular: {row['code']} → {ref} "
                f"(ya existe un camino {ref} → {row['code']})"
            )
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
    full = resolve_commit(commit)
    if full:
        commit = full

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
                resolve=False,
            )
        except NotFoundError:
            missing.append(code)
        else:
            linked.append(code)

    return {"commit": commit, "linked": linked, "missing": missing}
