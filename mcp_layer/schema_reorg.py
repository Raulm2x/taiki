"""Catálogo MCP de reorganización de tickets (plan y aplicación)."""

from __future__ import annotations

from ticketlib.domain import LINK_KINDS

from .schema_common import CODE, ToolSpec

ACTION = {
    "type": "string",
    "enum": ["move", "renumber", "promote", "demote", "purge"],
    "description": "Operación: mover de épica, renumerar, promover a épica, "
    "degradar a tarea o purgar cancelados",
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
    "description": "Épica destino para move o padre de la tarea degradada",
}
CHILDREN_TO = {
    "type": ["string", "null"],
    "maxLength": 40,
    "description": "Destino de las tareas al degradar: código de épica o "
    "'root' para dejarlas en la raíz",
}
RENUMBER = {
    "type": ["boolean", "null"],
    "description": "En move, renumerar con el prefijo del padre; por defecto "
    "se conserva el código",
}
INCLUDE_ADOPTED = {
    "type": "boolean",
    "description": "Incluye tickets adoptados al renumerar",
}
ALLOW_ROOT = {
    "type": "boolean",
    "description": "En renumber, permite renumerar una raíz publicada (con "
    "commits enlazados); sigue rechazando épicas con tareas y destinos "
    "ocupados",
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
    "description": "Tipo de enlace, tal como se guardó en el ticket",
}
LINK_REF = {
    "type": "string",
    "maxLength": 2_000,
    "description": "Referencia exacta del enlace (commit, código, ruta o URL)",
}
LINK_REASON = {
    "type": "string",
    "maxLength": 2_000,
    "description": "Motivo opcional que se anota en el evento desvincular",
}
REPOS = {
    "type": "array",
    "items": {"type": "string", "maxLength": 2_000},
    "maxItems": 50,
    "description": "Repos extra donde resolver commits de enlaces",
}
ROOT = {
    "type": ["string", "null"],
    "maxLength": 2_000,
    "description": "Raíz del checkout para resolver rutas y commits; por "
    "defecto la raíz actual",
}

REORG_SPECS = [
    ToolSpec(
        "reorg_plan",
        "Planifica move, renumber, promote, demote o purge sin escribir en la "
        "base de datos. Es un dry-run: revisa items y warnings y usa después "
        "reorg_apply con apply=true. Nunca reescribe eventos, comentarios ni "
        "mensajes históricos.",
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
        "Aplica una reorganización con apply=true. Para purge exige además "
        "force=true y hace backup previo. No reescribe el histórico: solo "
        "códigos, padre y referencias estructuradas. Usa reorg_plan primero "
        "para simular.",
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
        "Quita un enlace concreto de un ticket (code + kind + ref). Verifica "
        "que el enlace pertenece al ticket y registra el evento desvincular "
        "con el motivo; no borra en cascadas ni histórico.",
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
        "Detecta enlaces rotos: commits que ya no resuelven (raíz, repos y "
        "dotfiles), dependencias de tickets inexistentes y archivos "
        "ausentes cuyo padre se puede leer. Sin apply es un dry-run que "
        "lista broken; con apply=true retira los rotos con un evento "
        "desvincular por retirada. Un enlace depende de un ticket bloqueado "
        "exige force=true. Nunca borra tickets, solo enlaces.",
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
