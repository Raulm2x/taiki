"""Borrado y reparación de enlaces de tickets.

``unlink_ticket`` quita un enlace concreto verificando antes que pertenece
al ticket; ``repair_links`` detecta enlaces rotos tras reorg y reescrituras
(commits que ya no resuelven, dependencias de tickets inexistentes y
archivos ausentes) y los retira en una sola transacción. Nunca borra filas
de ``tickets`` ni histórico: solo filas de ``links``, con un evento
``desvincular`` por retirada que deja constancia del motivo y del actor.
"""

from __future__ import annotations

import os
import sqlite3
from pathlib import Path
from typing import Any, Iterable

from .. import db, workspace
from ..domain import (
    DEPENDENCY_KIND,
    LINK_KINDS,
    NotFoundError,
    ValidationError,
)
from ..gitutil import resolve_commit
from ..memory.evidence import commit_roots, resolve_evidence_commit
from ..util import check_enum, normalize_agent
from .common import event, row_by_code
from .queries import get_ticket

# Kinds que se pueden validar de forma determinista. ``external`` y el resto
# (``url``, ``sesion``, ``rama``, ``worktree``...) no tienen comprobación
# fiable y quedan fuera del escaneo en vez de marcarse rotos por defecto.
CHECKED_KINDS = (DEPENDENCY_KIND, "archivo", "commit")
_MARKS = ",".join("?" for _ in CHECKED_KINDS)


def unlink_ticket(
    conn: sqlite3.Connection,
    code: Any,
    kind: str,
    ref: str,
    actor: str = "agente",
    actor_type: str = "agente",
    session_id: str | None = None,
    *,
    reason: str | None = None,
) -> dict[str, Any]:
    """Quita un enlace concreto de un ticket.

    Verifica que el enlace pertenece al ticket antes de tocar nada: un
    ``kind``+``ref`` que no existe en ese ticket lanza ``NotFoundError`` en
    vez de borrar en cascada. Con ``kind="commit"`` el hash se resuelve antes
    de abrir la transacción, simétrico a ``link_ticket``; si el commit ya no
    resuelve (el caso típico de un enlace colgado), se busca tal cual.
    """
    check_enum(kind, LINK_KINDS, "tipo de enlace")
    ref = (ref or "").strip()
    if not ref:
        raise ValidationError("La referencia del enlace es obligatoria")
    actor = normalize_agent(actor)
    row = row_by_code(conn, code)
    if kind == "commit":
        full = resolve_commit(ref)
        if full:
            ref = full
    target = conn.execute(
        f"SELECT id FROM links WHERE ticket_id = ? AND kind = ? AND ref = ?",
        (row["id"], kind, ref),
    ).fetchone()
    if target is None:
        raise NotFoundError(
            f"El ticket {row['code']} no tiene el enlace {kind}: {ref}"
        )
    note = f"{kind}: {ref}"
    if reason:
        note = f"{note} — {reason.strip()}"
    with db.tx(conn):
        conn.execute("DELETE FROM links WHERE id = ?", (target["id"],))
        event(
            conn,
            row["id"],
            "desvincular",
            actor,
            actor_type,
            session_id,
            row["status"],
            row["status"],
            note,
        )
    return get_ticket(conn, code)


def _link_path(ref: str, root: Path) -> Path:
    """Resuelve una ruta de enlace relativa al checkout indicado."""
    path = Path(ref).expanduser()
    if not path.is_absolute():
        path = root / path
    return path


def _broken_reason(
    conn: sqlite3.Connection,
    kind: str,
    ref: str,
    roots: list[Path],
    root: Path,
) -> str | None:
    """Motivo por el que un enlace está roto, o ``None`` si está sano.

    ``archivo`` usa el mismo criterio que ``memory/repair.py``: una ruta
    ausente cuyo padre no se puede leer no se decide (M-0059 queda
    protegido) y no se marca como rota.
    """
    if kind == "commit":
        return (
            None
            if resolve_evidence_commit(ref, roots, root) is not None
            else "commit no resuelve en ningún repo conocido"
        )
    if kind == DEPENDENCY_KIND:
        found = conn.execute(
            "SELECT 1 FROM tickets WHERE code = ?", (ref,)
        ).fetchone()
        return None if found else "ticket dependiente inexistente"
    path = _link_path(ref, root)
    if path.exists():
        return None
    if not os.access(path.parent, os.R_OK | os.X_OK):
        return None
    return "archivo ausente"


def repair_links(
    conn: sqlite3.Connection,
    root: str | Path | None = None,
    repos: Iterable[Any] | None = None,
    apply: bool = False,
    force: bool = False,
    actor: str = "agente",
    actor_type: str = "agente",
    session_id: str | None = None,
) -> dict[str, Any]:
    """Detecta (y opcionalmente retira) enlaces rotos.

    Sin ``apply`` es un dry-run: solo devuelve ``broken``. Con ``apply``
    retira los rotos en una transacción, con un evento ``desvincular`` por
    retirada anotando motivo y actor. Un enlace ``depende`` de un ticket
    bloqueado no se retira sin ``force``: se reporta con
    ``retireable=false`` para que el conductor decida.
    """
    if root is not None:
        base = Path(str(root)).expanduser()
    else:
        base = Path(str(workspace.checkout_root())).expanduser()
    roots = commit_roots(base, repos)
    actor = normalize_agent(actor)
    checked = 0
    broken: list[dict[str, Any]] = []
    for row in conn.execute(
        f"SELECT l.id AS link_id, l.ticket_id AS ticket_id, l.kind AS kind, "
        f"l.ref AS ref, t.code AS code, t.status AS status "
        f"FROM links l JOIN tickets t ON t.id = l.ticket_id "
        f"WHERE l.kind IN ({_MARKS})",
        CHECKED_KINDS,
    ).fetchall():
        checked += 1
        reason = _broken_reason(conn, row["kind"], row["ref"], roots, base)
        if reason is None:
            continue
        item = {
            "code": row["code"],
            "kind": row["kind"],
            "ref": row["ref"],
            "reason": reason,
            "link_id": row["link_id"],
            "retireable": not (
                row["kind"] == DEPENDENCY_KIND
                and row["status"] == "bloqueado"
                and not force
            ),
        }
        broken.append(item)
    retired: list[dict[str, Any]] = []
    if apply:
        with db.tx(conn):
            # Se borra por el link_id capturado en el escaneo: la tabla
            # cambia dentro de la propia transacción y una segunda consulta
            # desalinearía filas y motivos.
            for item in broken:
                if not item["retireable"]:
                    continue
                holder = conn.execute(
                    "SELECT t.id AS ticket_id, t.status AS status "
                    "FROM links l JOIN tickets t ON t.id = l.ticket_id "
                    "WHERE l.id = ?",
                    (item["link_id"],),
                ).fetchone()
                if holder is None:
                    continue
                conn.execute(
                    "DELETE FROM links WHERE id = ?", (item["link_id"],)
                )
                event(
                    conn,
                    holder["ticket_id"],
                    "desvincular",
                    actor,
                    actor_type,
                    session_id,
                    holder["status"],
                    holder["status"],
                    f"{item['kind']}: {item['ref']} — {item['reason']}",
                )
                retired.append(
                    {key: item[key] for key in ("code", "kind", "ref", "reason")}
                )
    return {
        "checked": checked,
        "broken": broken,
        "retired": retired,
        "dry_run": not apply,
    }
