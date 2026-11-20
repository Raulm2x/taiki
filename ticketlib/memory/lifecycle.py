"""Ciclo de vida de memorias: patch, estados, feedback y supersesión."""

from __future__ import annotations

import json
import sqlite3
from typing import Any, Iterable

from .. import db
from ..domain import ValidationError
from ..store.common import UNSET
from ..util import check_enum, now
from . import core
from . import ledger as ledger_lib
from .curate import _set_status
from .models import (
    LIVE_STATUSES,
    MEMORY_KINDS,
    PLAYBOOK_DESCRIPTION_CHARS,
    PLAYBOOK_KIND,
    normalize_fingerprint,
    validate_playbook_key,
)


def patch_memory(
    conn: sqlite3.Connection,
    code: Any,
    *,
    title: Any = UNSET,
    description: Any = UNSET,
    content: Any = UNSET,
    key: Any = UNSET,
    kind: Any = UNSET,
    confidence: Any = UNSET,
    paths: Any = UNSET,
    kinds: Any = UNSET,
    ticket_kinds: Any = UNSET,
    tags: Any = UNSET,
    files: Any = UNSET,
    evidence: Any = UNSET,
    replace_evidence: bool = False,
    actor: str | None = None,
    session_id: str | None = None,
    reason: str | None = None,
    config: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Actualiza campos editables con validación, dedupe y ledger.

    ``evidence`` es aditiva por defecto: la evidencia histórica se acumula con
    ``INSERT OR IGNORE`` para no perder señales al corregir el contenido. El
    reemplazo es explícito con ``replace_evidence=True`` y queda en el snapshot
    del ledger, así que un borrado accidental se puede revertir con rollback.
    """
    cfg = core.config(config)
    row = core.memory_row(conn, code)
    current = dict(row)
    scope = str(current["scope"])
    updates: dict[str, Any] = {}
    final_kind = str(kind) if kind is not UNSET else str(current["kind"])
    if kind is not UNSET:
        check_enum(kind, MEMORY_KINDS, "kind")

    if title is not UNSET:
        updates["title"] = core.clean_text(
            title, "título", int(cfg["max_entry_chars"])
        )
    if description is not UNSET:
        description_limit = (
            PLAYBOOK_DESCRIPTION_CHARS
            if final_kind == PLAYBOOK_KIND
            else int(cfg["max_entry_chars"])
        )
        updates["description"] = core.clean_text(
            description,
            "descripción",
            description_limit,
            required=False,
        )
    if content is not UNSET:
        content_limit = (
            int(cfg["max_playbook_chars"])
            if final_kind == PLAYBOOK_KIND
            else int(cfg["max_entry_chars"])
        )
        updates["content"] = core.clean_text(
            content, "contenido", content_limit
        )
    if key is not UNSET:
        key_text = str(key or "").strip()
        if not key_text:
            raise ValidationError("La clave de la memoria no puede quedar vacía")
        updates["key"] = core.clean_key(key_text)
    if kind is not UNSET:
        updates["kind"] = kind
    if final_kind == PLAYBOOK_KIND:
        updates["key"] = validate_playbook_key(
            updates.get("key", current["key"])
        )
    if confidence is not UNSET:
        updates["confidence"] = core.confidence(confidence)
    if paths is not UNSET:
        project_paths = core.clean_project_paths(paths)
        updates["paths"] = (
            json.dumps(project_paths, ensure_ascii=False) if project_paths else None
        )
    if kinds is not UNSET:
        kind_list = core.clean_kinds(kinds)
        updates["kinds"] = (
            json.dumps(kind_list, ensure_ascii=False) if kind_list else None
        )
    if ticket_kinds is not UNSET:
        ticket_kind_list = core.clean_ticket_kinds(ticket_kinds)
        updates["ticket_kinds"] = (
            json.dumps(ticket_kind_list, ensure_ascii=False)
            if ticket_kind_list
            else None
        )
    if tags is not UNSET:
        tag_list = core.clean_tags(tags)
        updates["tags"] = (
            json.dumps(tag_list, ensure_ascii=False) if tag_list else None
        )
    file_rows = None if files is UNSET else core.clean_files(files, cfg)
    evidence_rows = None if evidence is UNSET else core.clean_evidence(evidence)

    if not updates and file_rows is None and evidence_rows is None:
        return core.receipt(conn, row, cfg)

    final_title = updates.get("title", current["title"])
    final_content = updates.get("content", current["content"])
    fingerprint = normalize_fingerprint(final_title, final_content)
    updates["fingerprint"] = fingerprint
    updates["updated_at"] = now()

    final_key = updates.get("key", current["key"])
    columns = ", ".join(f'"{name}" = ?' for name in updates)
    values = list(updates.values()) + [current["id"]]
    with db.tx(conn):
        if "key" in updates:
            clash = conn.execute(
                'SELECT * FROM memories WHERE scope = ? AND "key" = ? AND id != ? '
                "AND status IN ('active', 'proposed', 'stale') LIMIT 1",
                (scope, final_key, current["id"]),
            ).fetchone()
            if clash is not None:
                raise ValidationError(
                    f"Ya existe la memoria {clash['code']} con la clave "
                    f"{final_key!r}; usa supersede_memory o una clave distinta."
                )

        duplicate = conn.execute(
            "SELECT * FROM memories WHERE scope = ? AND fingerprint = ? AND id != ? "
            "AND status IN ('active', 'proposed', 'stale') LIMIT 1",
            (scope, fingerprint, current["id"]),
        ).fetchone()
        if duplicate is not None:
            raise ValidationError(
                f"Memoria duplicada: {duplicate['code']} ya tiene ese contenido."
            )

        before = ledger_lib.snapshot(conn, int(current["id"]))
        conn.execute(f"UPDATE memories SET {columns} WHERE id = ?", values)
        if file_rows is not None:
            conn.execute(
                "DELETE FROM memory_files WHERE memory_id = ?",
                (int(current["id"]),),
            )
            core.insert_files(conn, int(current["id"]), file_rows)
        if evidence_rows is not None:
            if replace_evidence:
                conn.execute(
                    "DELETE FROM memory_evidence WHERE memory_id = ?",
                    (int(current["id"]),),
                )
            core.insert_evidence(conn, int(current["id"]), evidence_rows)
        after = ledger_lib.snapshot(conn, int(current["id"]))
        ledger_lib.record(
            conn,
            action="update",
            memory_id=int(current["id"]),
            before=before,
            after=after,
            actor=actor,
            session_id=session_id,
            reason=reason,
        )
        freshness = core.evidence_freshness(
            conn,
            after["evidence"] if after is not None else [],
            after["files"] if after is not None else [],
            root=core.evidence_root(conn),
        )
        if not freshness["ok"] and str(current["status"]) == "active":
            _set_status(
                conn,
                dict(current),
                "stale",
                action="stale",
                reason="evidencia no vigente: " + "; ".join(freshness["reasons"]),
                actor=actor or "agente",
                session_id=session_id,
            )
        core.check_budget(conn, scope, cfg)
        updated = core.memory_row(conn, code)
        receipt = core.receipt(conn, updated, cfg)
        receipt["freshness"] = freshness
        return receipt


def _transition(
    conn: sqlite3.Connection,
    code: Any,
    *,
    status: str,
    action: str,
    allowed: tuple[str, ...],
    actor: str | None,
    session_id: str | None,
    reason: str | None,
    config: dict[str, Any] | None,
    archived: bool = False,
) -> dict[str, Any]:
    cfg = core.config(config)
    row = core.memory_row(conn, code)
    if row["status"] not in allowed:
        raise ValidationError(
            f"La memoria {row['code']} está en estado {row['status']!r}; "
            f"la acción {action} requiere {', '.join(allowed)}."
        )
    timestamp = now()
    with db.tx(conn):
        before = ledger_lib.snapshot(conn, int(row["id"]))
        if archived:
            conn.execute(
                "UPDATE memories SET status = ?, archived_at = ?, updated_at = ? "
                "WHERE id = ?",
                (status, timestamp, timestamp, int(row["id"])),
            )
        else:
            conn.execute(
                "UPDATE memories SET status = ?, archived_at = NULL, updated_at = ? "
                "WHERE id = ?",
                (status, timestamp, int(row["id"])),
            )
        after = ledger_lib.snapshot(conn, int(row["id"]))
        ledger_lib.record(
            conn,
            action=action,
            memory_id=int(row["id"]),
            before=before,
            after=after,
            actor=actor,
            session_id=session_id,
            reason=reason,
        )
        core.check_budget(conn, str(row["scope"]), cfg)
        updated = core.memory_row(conn, code)
        return core.receipt(conn, updated, cfg)


def approve_memory(
    conn: sqlite3.Connection,
    code: Any,
    *,
    actor: str | None = None,
    session_id: str | None = None,
    reason: str | None = None,
    config: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Aprobación humana: proposed -> active."""
    return _transition(
        conn,
        code,
        status="active",
        action="approve",
        allowed=("proposed",),
        actor=actor,
        session_id=session_id,
        reason=reason,
        config=config,
    )


def reject_memory(
    conn: sqlite3.Connection,
    code: Any,
    *,
    actor: str | None = None,
    session_id: str | None = None,
    reason: str | None = None,
    config: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Rechazo: proposed -> archived (el histórico queda en ledger)."""
    return _transition(
        conn,
        code,
        status="archived",
        action="reject",
        allowed=("proposed",),
        actor=actor,
        session_id=session_id,
        reason=reason,
        config=config,
        archived=True,
    )


def archive_memory(
    conn: sqlite3.Connection,
    code: Any,
    *,
    actor: str | None = None,
    session_id: str | None = None,
    reason: str | None = None,
    config: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Archiva una memoria activa o propuesta."""
    return _transition(
        conn,
        code,
        status="archived",
        action="archive",
        allowed=("active", "proposed", "stale"),
        actor=actor,
        session_id=session_id,
        reason=reason,
        config=config,
        archived=True,
    )


def restore_memory(
    conn: sqlite3.Connection,
    code: Any,
    *,
    actor: str | None = None,
    session_id: str | None = None,
    reason: str | None = None,
    config: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Restaura una memoria archivada a activa."""
    return _transition(
        conn,
        code,
        status="active",
        action="restore",
        allowed=("archived",),
        actor=actor,
        session_id=session_id,
        reason=reason,
        config=config,
    )


def pin_memory(
    conn: sqlite3.Connection,
    code: Any,
    *,
    pinned: bool = True,
    actor: str | None = None,
    session_id: str | None = None,
    reason: str | None = None,
    config: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Fija o desfija una memoria para el brief."""
    cfg = core.config(config)
    row = core.memory_row(conn, code)
    timestamp = now()
    with db.tx(conn):
        before = ledger_lib.snapshot(conn, int(row["id"]))
        conn.execute(
            "UPDATE memories SET pinned = ?, updated_at = ? WHERE id = ?",
            (1 if pinned else 0, timestamp, int(row["id"])),
        )
        after = ledger_lib.snapshot(conn, int(row["id"]))
        ledger_lib.record(
            conn,
            action="pin" if pinned else "unpin",
            memory_id=int(row["id"]),
            before=before,
            after=after,
            actor=actor,
            session_id=session_id,
            reason=reason,
        )
        updated = core.memory_row(conn, code)
        return core.receipt(conn, updated, cfg)


def feedback_memory(
    conn: sqlite3.Connection,
    code: Any,
    *,
    helpful: bool,
    note: str | None = None,
    actor: str | None = None,
    session_id: str | None = None,
    config: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Registra feedback y actualiza los contadores de utilidad."""
    cfg = core.config(config)
    row = core.memory_row(conn, code)
    column = "helpful_count" if helpful else "unhelpful_count"
    with db.tx(conn):
        before = ledger_lib.snapshot(conn, int(row["id"]))
        core.record_usage(
            conn,
            int(row["id"]),
            "feedback",
            session_id,
            helpful=helpful,
            note=note,
            touch=False,
        )
        conn.execute(
            f"UPDATE memories SET {column} = {column} + 1, updated_at = ? "
            "WHERE id = ?",
            (now(), int(row["id"])),
        )
        after = ledger_lib.snapshot(conn, int(row["id"]))
        ledger_lib.record(
            conn,
            action="feedback",
            memory_id=int(row["id"]),
            before=before,
            after=after,
            actor=actor,
            session_id=session_id,
            reason=note,
        )
        updated = core.memory_row(conn, code)
        return core.receipt(conn, updated, cfg)


def supersede_memory(
    conn: sqlite3.Connection,
    code: Any,
    by: Any,
    *,
    actor: str | None = None,
    session_id: str | None = None,
    reason: str | None = None,
    config: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Marca ``code`` como superseded por ``by`` y enlaza ambas."""
    cfg = core.config(config)
    old = core.memory_row(conn, code)
    new = core.memory_row(conn, by)
    if int(old["id"]) == int(new["id"]):
        raise ValidationError("Una memoria no puede sustituirse a sí misma")
    if old["status"] == "superseded":
        raise ValidationError(f"La memoria {old['code']} ya está superseded")
    if new["status"] in ("archived", "superseded"):
        raise ValidationError(f"La memoria sustituta {new['code']} no está vigente")
    timestamp = now()
    with db.tx(conn):
        before_old = ledger_lib.snapshot(conn, int(old["id"]))
        before_new = ledger_lib.snapshot(conn, int(new["id"]))
        if before_old is not None:
            before_old["counterpart"] = before_new
        conn.execute(
            "UPDATE memories SET status = 'superseded', superseded_by = ?, "
            "updated_at = ? WHERE id = ?",
            (new["code"], timestamp, int(old["id"])),
        )
        conn.execute(
            "UPDATE memories SET supersedes_id = COALESCE(supersedes_id, ?) "
            "WHERE id = ?",
            (int(old["id"]), int(new["id"])),
        )
        after_old = ledger_lib.snapshot(conn, int(old["id"]))
        after_new = ledger_lib.snapshot(conn, int(new["id"]))
        if after_old is not None:
            after_old["counterpart"] = after_new
        ledger_lib.record(
            conn,
            action="supersede",
            memory_id=int(old["id"]),
            before=before_old,
            after=after_old,
            actor=actor,
            session_id=session_id,
            reason=reason or f"sustituida por {new['code']}",
            meta={"by": new["code"], "counterpart_id": int(new["id"])},
        )
        ledger_lib.record(
            conn,
            action="update",
            memory_id=int(new["id"]),
            before=before_new,
            after=after_new,
            actor=actor,
            session_id=session_id,
            reason=f"enlazada como sustituta de {old['code']}",
            meta={"counterpart_id": int(old["id"])},
        )
        updated = core.memory_row(conn, old["code"])
        return core.receipt(conn, updated, cfg)


def merge_memories(
    conn: sqlite3.Connection,
    winner: Any,
    losers: Iterable[Any],
    *,
    actor: str | None = None,
    session_id: str | None = None,
    reason: str | None = None,
    allow_protected: bool = False,
    config: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Consolida N memorias en ``winner`` sin borrar ninguna fila.

    Mismo scope obligatorio, evidencia y ficheros absorbidos con
    ``INSERT OR IGNORE``, perdedoras archivadas con motivo y asiento de ledger
    con snapshot antes/después por memoria, igual que ``curate._merge``.
    Pin y lock son decisiones humanas: sin ``allow_protected`` la fusión se
    rechaza si alguna memoria está protegida.
    """
    cfg = core.config(config)
    winner_row = core.memory_row(conn, winner)
    winner_id = int(winner_row["id"])
    winner_code = str(winner_row["code"])
    if isinstance(losers, (str, int)):
        losers = [losers]
    loser_rows: list[sqlite3.Row] = []
    seen = {winner_id}
    for item in losers:
        row = core.memory_row(conn, item)
        memory_id = int(row["id"])
        if memory_id == winner_id:
            raise ValidationError(
                f"La memoria ganadora {winner_code} no puede ser también perdedora"
            )
        if memory_id in seen:
            continue
        seen.add(memory_id)
        loser_rows.append(row)
    if not loser_rows:
        raise ValidationError("La fusión requiere al menos una memoria perdedora")
    scope = str(winner_row["scope"])
    for row in [winner_row, *loser_rows]:
        if str(row["scope"]) != scope:
            raise ValidationError(
                f"No se pueden fusionar scopes distintos: {winner_code} es "
                f"{scope!r} y {row['code']} es {row['scope']!r}."
            )
        if str(row["status"]) not in LIVE_STATUSES:
            raise ValidationError(
                f"La memoria {row['code']} está en estado {row['status']!r}; "
                "la fusión solo admite memorias vigentes."
            )
    protected = [
        str(row["code"])
        for row in [winner_row, *loser_rows]
        if row["pinned"] or row["locked"]
    ]
    if protected and not allow_protected:
        raise ValidationError(
            f"Memorias protegidas ({', '.join(protected)}): pin/lock son "
            "decisiones humanas; usa allow_protected solo con autorización."
        )
    loser_codes = [str(row["code"]) for row in loser_rows]
    merged: list[dict[str, str]] = []
    with db.tx(conn):
        before_winner = ledger_lib.snapshot(conn, winner_id)
        for row in loser_rows:
            loser_id = int(row["id"])
            conn.execute(
                "INSERT OR IGNORE INTO memory_evidence("
                "memory_id, kind, ref, created_at) "
                "SELECT ?, kind, ref, created_at FROM memory_evidence "
                "WHERE memory_id = ?",
                (winner_id, loser_id),
            )
            conn.execute(
                "INSERT OR IGNORE INTO memory_files("
                "memory_id, path, content, sha256) "
                "SELECT ?, path, content, sha256 FROM memory_files "
                "WHERE memory_id = ?",
                (winner_id, loser_id),
            )
        conn.execute(
            "UPDATE memories SET updated_at = ? WHERE id = ?",
            (now(), winner_id),
        )
        after_winner = ledger_lib.snapshot(conn, winner_id)
        ledger_lib.record(
            conn,
            action="update",
            memory_id=winner_id,
            before=before_winner,
            after=after_winner,
            actor=actor,
            session_id=session_id,
            reason=reason
            or f"absorbidas evidencias de {', '.join(loser_codes)}",
            meta={
                "merged": loser_codes,
                "counterpart_ids": [int(row["id"]) for row in loser_rows],
            },
        )
        for row in loser_rows:
            merge_reason = reason or f"fusionada en {winner_code}"
            _set_status(
                conn,
                dict(row),
                "archived",
                action="merge",
                reason=merge_reason,
                meta={"into": winner_code, "counterpart_id": winner_id},
                actor=actor or "agente",
                session_id=session_id,
            )
            merged.append(
                {
                    "code": str(row["code"]),
                    "into": winner_code,
                    "reason": merge_reason,
                }
            )
        core.check_budget(conn, scope, cfg)
        receipt = core.receipt(conn, core.memory_row(conn, winner_code), cfg)
        receipt["merged"] = merged
        return receipt


__all__ = [
    "approve_memory",
    "archive_memory",
    "feedback_memory",
    "merge_memories",
    "patch_memory",
    "pin_memory",
    "reject_memory",
    "restore_memory",
    "supersede_memory",
]
