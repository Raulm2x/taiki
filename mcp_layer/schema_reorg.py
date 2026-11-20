"""Catálogo MCP de reorganización de tickets (plan y aplicación)."""

from __future__ import annotations

from ticketlib.domain import LINK_KINDS

from .schema_common import CODE, ToolSpec

ACTION = {
    "type": "string",
    "enum": ["move", "renumber", "promote", "demote", "purge"],
    "description": "move/renumber/promote/demote/purge",
}
CODES = {
    "type": "array",
    "items": {"type": "string", "maxLength": 40},
    "minItems": 0,
    "maxItems": 200,
    "description": "Códigos afectados; renumber sin codes revisa todos",
}
PARENT = {
    "type": ["string", "null"],
    "maxLength": 40,
    "description": "Épica destino (move) o padre (demote)",
}
CHILDREN_TO = {
    "type": ["string", "null"],
    "maxLength": 40,
    "description": "Destino de las hijas: épica o 'root'",
}
RENUMBER = {
    "type": ["boolean", "null"],
    "description": "En move, renumerar con el prefijo del padre",
}
INCLUDE_ADOPTED = {
    "type": "boolean",
    "description": "Incluye tickets adoptados al renumerar",
}
ALLOW_ROOT = {
    "type": "boolean",
    "description": "En renumber, permite una raíz publicada (con commits)",
}
APPLY = {
    "type": "boolean",
    "description": "Debe ser true para escribir; sin apply usa reorg_plan",
}
FORCE = {
    "type": "boolean",
    "description": "Confirmación obligatoria para purgar con reorg_apply",
}
LINK_KIND = {
    "type": "string",
    "enum": list(LINK_KINDS),
    "description": "Tipo de enlace, tal como se guardó",
}
LINK_REF = {
    "type": "string",
    "maxLength": 2_000,
    "description": "Referencia exacta del enlace",
}
LINK_REASON = {
    "type": "string",
    "maxLength": 2_000,
    "description": "Motivo anotado en el evento desvincular",
}
REPOS = {
    "type": "array",
    "items": {"type": "string", "maxLength": 2_000},
    "maxItems": 50,
    "description": "Repos extra donde resolver commits",
}
ROOT = {
    "type": ["string", "null"],
    "maxLength": 2_000,
    "description": "Raíz del checkout (defecto: la actual)",
}

REORG_SPECS = [
    ToolSpec(
        "reorg_plan",
        "Dry-run de la reorganización: revisa items y warnings y aplica "
        "después con reorg_apply.",
        {
            "action": ACTION,
            "codes": CODES,
            "parent": PARENT,
            "children_to": CHILDREN_TO,
            "include_adopted": INCLUDE_ADOPTED,
            "renumber": RENUMBER,
            "allow_root": ALLOW_ROOT,
        },
        ("action",),
        read_only=True,
    ),
    ToolSpec(
        "reorg_apply",
        "Aplica la reorganización con apply=true; purge exige force y hace "
        "backup. No reescribe el histórico.",
        {
            "action": ACTION,
            "codes": CODES,
            "parent": PARENT,
            "children_to": CHILDREN_TO,
            "include_adopted": INCLUDE_ADOPTED,
            "renumber": RENUMBER,
            "allow_root": ALLOW_ROOT,
            "apply": APPLY,
            "force": FORCE,
        },
        ("action", "apply"),
    ),
    ToolSpec(
        "unlink",
        "Quita un enlace concreto del ticket verificando que es suyo; "
        "registra el evento desvincular.",
        {
            "code": CODE,
            "kind": LINK_KIND,
            "ref": LINK_REF,
            "reason": LINK_REASON,
        },
        ("code", "kind", "ref"),
    ),
    ToolSpec(
        "links_repair",
        "Detecta enlaces rotos (commits que no resuelven, depende de "
        "tickets inexistentes, archivos ausentes); sin apply lista y con "
        "apply=true retira; depende de bloqueado exige force=true.",
        {
            "root": ROOT,
            "repos": REPOS,
            "apply": APPLY,
            "force": FORCE,
        },
        (),
    ),
]

__all__ = ["REORG_SPECS"]
