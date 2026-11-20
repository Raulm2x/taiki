"""Export/import de memoria: snapshot, checksum, roundtrip y secretos."""

from __future__ import annotations

import json

from ticketlib import db
from ticketlib.domain import ValidationError
from ticketlib.memory import add_memory, create_playbook, patch_memory
from ticketlib.memory.export import export_snapshot, import_snapshot, rollback
from tests.support import Harness


def _seed(conn) -> dict[str, dict]:
    active = add_memory(
        conn,
        title="Memoria exportable",
        content="Contenido estable.",
        kind="convention",
        key="export-active",
        source="user",
        evidence=[{"kind": "ticket", "ref": "T-0048.04"}],
    )
    playbook = create_playbook(
        conn,
        slug="export-playbook",
        title="Playbook exportable",
        description="Procedimiento de prueba.",
        content="Paso uno y paso dos.",
        evidence=[{"kind": "ticket", "ref": "T-0048.04"}],
        files=[
            {
                "path": "references/nota.md",
                "content": "Contenido de apoyo.",
            }
        ],
        source="user",
    )
    return {"active": active, "playbook": playbook}


def _export(h: Harness) -> None:
    conn = h.conn
    codes = _seed(conn)
    first = h.tmp / "snapshot.json"
    result = export_snapshot(conn, first, "json")
    document = json.loads(first.read_text(encoding="utf-8"))
    h.ok(
        result["version"] == 1
        and result["checksum"].startswith("sha256:")
        and document["checksum"] == result["checksum"],
        "el snapshot declara versión e incluye checksum verificable",
    )
    h.ok(
        result["memories"] == 2
        and result["playbooks"] == 1
        and document["format"] == "taiki-memory"
        and any(
            item["code"] == codes["playbook"]["code"]
            and item["files"]
            and item["evidence"]
            for item in document["memories"]
        ),
        "el snapshot cuenta memorias y playbooks e incluye ficheros y evidencia",
    )

    second = h.tmp / "snapshot-2.json"
    export_snapshot(conn, second, "json")
    again = json.loads(second.read_text(encoding="utf-8"))
    h.ok(
        document["memories"] == again["memories"]
        and document["checksum"] == again["checksum"],
        "la exportación es determinista en contenido y checksum",
    )

    markdown = h.tmp / "snapshot.md"
    md = export_snapshot(conn, markdown, "markdown")
    text = markdown.read_text(encoding="utf-8")
    h.ok(
        md["format"] == "markdown"
        and "Taiki memory snapshot" in text
        and codes["active"]["code"] in text
        and "taiki-memory-json:" in text,
        "el Markdown incluye resumen y JSON embebido",
    )

    secret_conn = db.connect(h.tmp / "export-secret.db")
    try:
        secret = add_memory(
            secret_conn,
            title="Memoria con secreto",
            content="Contenido inocente.",
            key="export-secret",
            source="user",
        )
        secret_conn.execute(
            "UPDATE memories SET content = ? WHERE code = ?",
            ("password: hunter2", secret["code"]),
        )
        secret_conn.commit()
        secret_path = h.tmp / "secret.json"
        export_snapshot(secret_conn, secret_path, "json")
        exported = secret_path.read_text(encoding="utf-8")
        stored = json.loads(exported)
        memory = next(
            item for item in stored["memories"] if item["code"] == secret["code"]
        )
        h.ok(
            "hunter2" not in exported and "[redactado]" in memory["content"],
            "la exportación redacta secretos",
        )
    finally:
        secret_conn.close()


def _roundtrip(h: Harness) -> None:
    source = db.connect(h.tmp / "roundtrip-source.db")
    try:
        codes = _seed(source)
        snapshot = h.tmp / "roundtrip.json"
        export_snapshot(source, snapshot, "json")
    finally:
        source.close()
    target = db.connect(h.tmp / "import-target.db")
    try:
        report = import_snapshot(target, snapshot, strategy="keep")
        h.ok(
            len(report["imported"]) == 2 and report["conflicts"] == [],
            "el roundtrip importa todas las memorias sin conflictos",
        )
        stored = {
            row["code"]: dict(row)
            for row in target.execute("SELECT * FROM memories")
        }
        playbook = stored[codes["playbook"]["code"]]
        h.ok(
            stored[codes["active"]["code"]]["content"] == "Contenido estable."
            and playbook["kind"] == "playbook"
            and playbook["status"] == codes["playbook"]["status"]
            and target.execute(
                "SELECT 1 FROM memory_evidence WHERE memory_id = ?",
                (playbook["id"],),
            ).fetchone()
            is not None
            and target.execute(
                "SELECT 1 FROM memory_files WHERE memory_id = ?",
                (playbook["id"],),
            ).fetchone()
            is not None,
            "el roundtrip conserva contenido, tipo, estado, evidencia y ficheros",
        )
        next_memory = add_memory(
            target,
            title="Memoria posterior",
            content="No reutiliza códigos.",
            key="posterior",
            source="user",
        )
        h.ok(
            next_memory["code"] not in stored,
            "el contador de códigos avanza tras importar",
        )
    finally:
        target.close()

    target_md = db.connect(h.tmp / "import-target-md.db")
    try:
        md_report = import_snapshot(
            target_md, h.tmp / "snapshot.md", strategy="keep"
        )
        h.eq(
            len(md_report["imported"]),
            2,
            "el Markdown embebido también se puede importar",
        )
    finally:
        target_md.close()


def _modes(h: Harness) -> None:
    source = db.connect(h.tmp / "import-source.db")
    try:
        add_memory(
            source,
            title="Entrante",
            content="Contenido entrante.",
            key="entrante",
            source="user",
        )
        snapshot = h.tmp / "modes.json"
        export_snapshot(source, snapshot, "json")
    finally:
        source.close()

    target = db.connect(h.tmp / "import-modes.db")
    try:
        existing = add_memory(
            target,
            title="Existente",
            content="Contenido existente.",
            key="existente",
            source="user",
        )
        kept = import_snapshot(target, snapshot, strategy="keep")
        h.ok(
            kept["skipped"] == ["M-0001"]
            and target.execute(
                "SELECT content FROM memories WHERE code = ?", (existing["code"],)
            ).fetchone()[0]
            == "Contenido existente.",
            "keep omite el conflicto y conserva lo existente",
        )
        replaced = import_snapshot(target, snapshot, strategy="replace")
        h.ok(
            replaced["updated"] == ["M-0001"]
            and replaced["conflicts"][0]["resolution"] == "replaced"
            and target.execute(
                "SELECT content FROM memories WHERE code = ?", (existing["code"],)
            ).fetchone()[0]
            == "Contenido entrante.",
            "replace actualiza la memoria conflictiva",
        )
    finally:
        target.close()

    fresh = db.connect(h.tmp / "import-new.db")
    try:
        add_memory(
            fresh,
            title="Existente new",
            content="Contenido existente.",
            key="existente-new",
            source="user",
        )
        added = import_snapshot(fresh, snapshot, strategy="new")
        h.ok(
            added["imported"] == ["M-0002"]
            and added["conflicts"][0]["resolution"] == "new_code",
            "new inserta con un código nuevo",
        )
    finally:
        fresh.close()

    dry = db.connect(h.tmp / "import-dry.db")
    try:
        before = dry.execute("SELECT COUNT(*) FROM memories").fetchone()[0]
        ledgers = dry.execute("SELECT COUNT(*) FROM memory_ledger").fetchone()[0]
        report = import_snapshot(dry, snapshot, strategy="keep", dry_run=True)
        h.ok(
            report["dry_run"]
            and len(report["imported"]) == 1
            and dry.execute("SELECT COUNT(*) FROM memories").fetchone()[0] == before
            and dry.execute("SELECT COUNT(*) FROM memory_ledger").fetchone()[0]
            == ledgers,
            "dry-run informa del plan sin escribir",
        )
    finally:
        dry.close()

    tampered = json.loads((h.tmp / "modes.json").read_text(encoding="utf-8"))
    tampered["memories"][0]["content"] = "contenido alterado"
    broken = h.tmp / "tampered.json"
    broken.write_text(json.dumps(tampered), encoding="utf-8")
    verify = db.connect(h.tmp / "import-tampered.db")
    try:
        h.raises(
            ValidationError,
            lambda: import_snapshot(verify, broken, strategy="keep"),
            "un checksum alterado se rechaza",
        )
    finally:
        verify.close()


def _rollback_snapshot(h: Harness) -> None:
    conn = db.connect(h.tmp / "rollback-snapshot.db")
    try:
        memory = add_memory(
            conn,
            title="Original",
            content="Contenido original.",
            key="rollback-snapshot",
            source="user",
        )
        snapshot = h.tmp / "rollback.json"
        export_snapshot(conn, snapshot, "json")
        patch_memory(conn, memory["code"], content="Contenido cambiado.")
        extra = add_memory(
            conn,
            title="Extra posterior",
            content="No estaba en el snapshot.",
            key="rollback-extra",
            source="user",
        )
        dry = rollback(conn, snapshot=snapshot, dry_run=True)
        h.ok(
            dry["mode"] == "snapshot"
            and conn.execute(
                "SELECT content FROM memories WHERE code = ?", (memory["code"],)
            ).fetchone()[0]
            == "Contenido cambiado.",
            "el rollback por snapshot admite dry-run",
        )
        result = rollback(conn, snapshot=snapshot)
        restored = conn.execute(
            "SELECT content FROM memories WHERE code = ?", (memory["code"],)
        ).fetchone()[0]
        extra_status = conn.execute(
            "SELECT status FROM memories WHERE code = ?", (extra["code"],)
        ).fetchone()[0]
        ledger = conn.execute(
            "SELECT 1 FROM memory_ledger WHERE action = 'rollback' LIMIT 1"
        ).fetchone()
        h.ok(
            result["mode"] == "snapshot"
            and restored == "Contenido original."
            and extra_status == "archived"
            and extra["code"] in result["archived"]
            and ledger is not None,
            "el rollback por snapshot restaura, archiva lo posterior y audita",
        )
    finally:
        conn.close()


def run(h: Harness) -> None:
    _export(h)
    _roundtrip(h)
    _modes(h)
    _rollback_snapshot(h)
