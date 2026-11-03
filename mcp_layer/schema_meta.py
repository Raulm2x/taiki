"""Catálogo MCP de skills, recursos y prompts."""

from __future__ import annotations

from .schema_common import ToolSpec

SKILL_SPECS = [
    ToolSpec(
        "skills_suggest",
        "Sugiere skills curadas de AutoSkills para el stack del proyecto o "
        "para las tecnologías indicadas. Devuelve fuente, hash, revisión y "
        "comprobación de seguridad; nunca instala ni modifica nada. Solo lectura.",
        {
            "techs": {
                "type": "array",
                "items": {"type": "string", "maxLength": 80},
                "maxItems": 20,
                "description": (
                    "Tecnologías a consultar; si se omite se detecta el stack "
                    "del proyecto sin red"
                ),
            },
            "limit": {
                "type": "integer",
                "minimum": 1,
                "maximum": 100,
                "default": 20,
                "description": "Máximo de sugerencias devueltas",
            },
        },
        read_only=True,
    ),
]

RESOURCES = [
    {
        "uri": "taiki://inbox",
        "name": "Bandeja de entrada",
        "description": "Tickets abiertos en resumen (máximo 20; usa list para el resto)",
        "mimeType": "application/json",
    },
    {
        "uri": "taiki://stats",
        "name": "Estadísticas",
        "description": "Conteos por estado, tipo, prioridad y proyecto",
        "mimeType": "application/json",
    },
    {
        "uri": "taiki://brief",
        "name": "Brief operativo",
        "description": "Memoria vigente acotada: proyecto, usuario y routing observado",
        "mimeType": "text/markdown",
    },
    {
        "uri": "taiki://memory/candidates",
        "name": "Candidatos de memoria",
        "description": "Candidatos deterministas de aprendizaje del histórico (máximo 20)",
        "mimeType": "application/json",
    },
]

RESOURCE_TEMPLATES = [
    {
        "uriTemplate": "taiki://ticket/{code}",
        "name": "Ticket",
        "description": "Detalle completo de un ticket por código (T-0001)",
        "mimeType": "application/json",
    },
    {
        "uriTemplate": "taiki://memory/{code}",
        "name": "Memoria",
        "description": "Detalle de una memoria por código (M-0001)",
        "mimeType": "application/json",
    },
    {
        "uriTemplate": "taiki://playbook/{slug}",
        "name": "Playbook",
        "description": "Procedimiento y ficheros de apoyo por slug",
        "mimeType": "application/json",
    },
]

PROMPTS = [
    {
        "name": "next",
        "description": "Selecciona el siguiente ticket pendiente y guía el trabajo",
        "arguments": [
            {
                "name": "project",
                "description": "Filtrar por proyecto",
                "required": False,
            }
        ],
    },
    {
        "name": "review",
        "description": "Lista tickets completados o verificados pendientes de confirmación del usuario",
        "arguments": [],
    },
    {
        "name": "taiki:brief",
        "description": "Aplica el brief operativo de memoria al empezar el trabajo",
        "arguments": [
            {
                "name": "section",
                "description": "project, user o all (por defecto all)",
                "required": False,
            }
        ],
    },
    {
        "name": "taiki:curate",
        "description": "Revisa candidatos y propone lecciones sin tocar código ni aprobar",
        "arguments": [],
    },
    {
        "name": "taiki:playbook",
        "description": "Carga un playbook como procedimiento y recuerda revisar sus pitfalls",
        "arguments": [
            {
                "name": "slug",
                "description": "Slug del playbook, por ejemplo revisar-pr",
                "required": True,
            }
        ],
    },
]
