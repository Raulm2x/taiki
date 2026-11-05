"""Catálogo MCP de reorganización de tickets (plan y aplicación)."""

from __future__ import annotations

from .schema_common import ToolSpec

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
APPLY = {
    "type": "boolean",
    "description": "Debe ser true para escribir; sin apply usa reorg_plan",
}
FORCE = {
    "type": "boolean",
    "description": "Confirmación obligatoria para purgar con reorg_apply",
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
            "apply": APPLY,
            "force": FORCE,
        },
        ("action", "apply"),
    ),
]

__all__ = ["REORG_SPECS"]
