"""Catálogo MCP de herramientas de memoria."""

from __future__ import annotations

from ticketlib.domain import KINDS
from ticketlib.memory.advice import CHECKLIST_KINDS
from ticketlib.memory.models import (
    MEMORY_KINDS,
    MEMORY_SCOPES,
    MEMORY_STATUSES,
)

from .schema_common import ToolSpec

MEMORY_SPECS = [
    ToolSpec(
        "brief",
        "Brief operativo de memoria: datos vigentes acotados por presupuesto "
        "(proyecto, usuario y routing observado). Aplícalo al empezar para "
        "recuperar contexto sin arrastrar todo el historial. Con write=true "
        "genera .taiki/generated/brief.md.",
        {
            "budget": {
                "type": "integer",
                "minimum": 1,
                "maximum": 100_000,
                "description": "Presupuesto de caracteres; por defecto el del workspace",
            },
            "section": {
                "type": "string",
                "enum": ["project", "user", "all"],
                "default": "all",
                "description": "Sección del brief a renderizar",
            },
            "write": {
                "type": "boolean",
                "default": False,
                "description": "Escribe el artefacto .taiki/generated/brief.md",
            },
        },
        read_only=True,
    ),
    ToolSpec(
        "memory_search",
        "Busca memorias vigentes por texto y filtra por scope, tipo, estado, "
        "rutas, kinds o ticket_kinds. Devuelve snippets compactos; usa "
        "include_content=true solo cuando necesites el contenido completo.",
        {
            "query": {
                "type": "string",
                "maxLength": 500,
                "description": "Texto libre; sin query se listan por prioridad",
            },
            "scope": {
                "type": "string",
                "enum": list(MEMORY_SCOPES),
                "description": "Scope de la memoria",
            },
            "kind": {
                "type": "string",
                "enum": list(MEMORY_KINDS),
                "description": "Tipo principal de la memoria",
            },
            "status": {
                "type": "string",
                "enum": list(MEMORY_STATUSES),
                "description": "Estado; por defecto excluye archivadas y sustituidas",
            },
            "paths": {
                "type": "array",
                "items": {"type": "string", "maxLength": 500},
                "maxItems": 100,
                "description": "Rutas del proyecto que debe tocar la memoria",
            },
            "kinds": {
                "type": "array",
                "items": {"type": "string", "enum": list(MEMORY_KINDS)},
                "maxItems": 20,
                "description": "Tipos secundarios de memoria que debe incluir",
            },
            "ticket_kinds": {
                "type": "array",
                "items": {"type": "string", "enum": list(KINDS)},
                "maxItems": 20,
                "description": (
                    "Tipos de ticket aplicables; las memorias sin etiqueta son "
                    "generales y también casan"
                ),
            },
            "limit": {
                "type": "integer",
                "minimum": 1,
                "maximum": 100,
                "default": 10,
                "description": "Máximo de memorias devueltas",
            },
            "include_content": {
                "type": "boolean",
                "default": False,
                "description": "Devuelve el contenido completo en vez de snippet",
            },
        },
        read_only=True,
    ),
    ToolSpec(
        "memory_checklist",
        "Checklist acotado de pitfalls y convenciones activos para revisar un "
        "cambio. kind filtra por tipo de ticket y memory_kind por tipo de "
        "memoria (pitfall o convention); paths filtra por rutas. Cada entrada "
        "es compacta y lleva evidencia. Solo lectura.",
        {
            "kind": {
                "type": "string",
                "enum": list(KINDS),
                "description": "Tipo de ticket: bug, funcionalidad, etc.",
            },
            "memory_kind": {
                "type": "string",
                "enum": list(CHECKLIST_KINDS),
                "description": "Tipo de memoria: pitfall o convention",
            },
            "paths": {
                "type": "array",
                "items": {"type": "string", "maxLength": 500},
                "maxItems": 100,
                "description": "Rutas que toca el cambio",
            },
            "limit": {
                "type": "integer",
                "minimum": 1,
                "maximum": 20,
                "default": 5,
                "description": "Máximo de entradas del checklist",
            },
        },
        read_only=True,
    ),
    ToolSpec(
        "memory_playbook",
        "Carga un playbook (procedimiento) aprobado por su slug. Devuelve el "
        "cuerpo, los metadatos y la lista de ficheros de apoyo; usa path para "
        "leer un apoyo concreto sin volcar todo el skill. Solo lectura.",
        {
            "slug": {
                "type": "string",
                "maxLength": 64,
                "description": "Slug del playbook, por ejemplo revisar-pr",
            },
            "path": {
                "type": "string",
                "maxLength": 500,
                "description": "Fichero de apoyo a cargar (references/, scripts/, assets/ o templates/)",
            },
        },
        ("slug",),
        read_only=True,
    ),
    ToolSpec(
        "memory_write",
        "Escribe o mantiene memorias y playbooks. Cada operación se aplica con "
        "la política de escritura del workspace: las propuestas quedan "
        "pendientes de aprobación humana. Usa evidence (ticket, commit, file, "
        "url o session) cuando propongas una lección; no guardes fallos "
        "transitorios, secretos ni prohibiciones amplias. Con kind=playbook "
        "indica key=playbook:<slug>, description y, si aplica, files.",
        {
            "target": {
                "type": "string",
                "enum": list(MEMORY_SCOPES),
                "default": "project",
                "description": "Scope de las memorias nuevas",
            },
            "operations": {
                "type": "array",
                "maxItems": 20,
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["action"],
                    "properties": {
                        "action": {
                            "type": "string",
                            "enum": [
                                "add",
                                "patch",
                                "remove",
                                "supersede",
                                "feedback",
                            ],
                            "description": "Operación sobre la memoria",
                        },
                        "code": {
                            "type": "string",
                            "maxLength": 40,
                            "description": "Código M-0001 para patch/remove/supersede/feedback",
                        },
                        "kind": {
                            "type": "string",
                            "enum": list(MEMORY_KINDS),
                            "description": "Tipo principal de memoria",
                        },
                        "ticket_kinds": {
                            "type": "array",
                            "items": {"type": "string", "enum": list(KINDS)},
                            "maxItems": 20,
                            "description": (
                                "Tipos de ticket aplicables; vacío o ausente "
                                "significa general"
                            ),
                        },
                        "key": {
                            "type": "string",
                            "maxLength": 200,
                            "description": "Clave estable; una nueva reemplaza la vigente",
                        },
                        "title": {
                            "type": "string",
                            "maxLength": 600,
                            "description": "Título breve y accionable",
                        },
                        "content": {
                            "type": "string",
                            "maxLength": 12_000,
                            "description": "Contenido destilado; en un playbook es el procedimiento",
                        },
                        "description": {
                            "type": "string",
                            "maxLength": 600,
                            "description": "Resumen; en un playbook se limita a 240 caracteres",
                        },
                        "files": {
                            "type": "array",
                            "maxItems": 50,
                            "items": {
                                "type": "object",
                                "additionalProperties": False,
                                "required": ["path", "content"],
                                "properties": {
                                    "path": {
                                        "type": "string",
                                        "maxLength": 500,
                                        "description": "Ruta interna: references/, scripts/, assets/ o templates/",
                                    },
                                    "content": {
                                        "type": "string",
                                        "maxLength": 12_000,
                                        "description": "Contenido de texto del fichero de apoyo",
                                    },
                                },
                            },
                            "description": "Ficheros de apoyo de un playbook",
                        },
                        "evidence": {
                            "type": "array",
                            "maxItems": 20,
                            "items": {"type": ["string", "object"]},
                            "description": "Referencias que respaldan la memoria",
                        },
                        "reason": {
                            "type": "string",
                            "maxLength": 2_000,
                            "description": "Motivo del cambio o de la lección",
                        },
                        "confidence": {
                            "type": "number",
                            "minimum": 0,
                            "maximum": 1,
                            "description": "Confianza entre 0 y 1",
                        },
                        "pin": {
                            "type": "boolean",
                            "description": "Fija o desfija la memoria en el brief",
                        },
                        "helpful": {
                            "type": "boolean",
                            "description": "Feedback: true si la memoria ayudó",
                        },
                    },
                },
                "description": "Operaciones a aplicar en orden",
            },
        },
        ("operations",),
    ),
    ToolSpec(
        "memory_candidates",
        "Candidatos de aprendizaje deterministas a partir del histórico. "
        "mode=list devuelve candidatos compactos (rework, rechazos, bloqueos, "
        "cierres costosos, routing, hotspots y directivas); mode=review "
        "devuelve el paquete completo para el curator con memorias actuales, "
        "tickets recientes, umbrales, reglas e instrucciones. Solo lectura.",
        {
            "mode": {
                "type": "string",
                "enum": ["list", "review"],
                "default": "list",
                "description": "list para candidatos; review para el paquete",
            },
            "since": {
                "type": "string",
                "maxLength": 40,
                "description": "Ventana temporal: 90d o una fecha ISO",
            },
            "kind": {
                "type": "string",
                "enum": list(MEMORY_KINDS),
                "description": "Filtra por tipo de memoria sugerida",
            },
            "limit": {
                "type": "integer",
                "minimum": 1,
                "maximum": 100,
                "default": 20,
                "description": "Máximo de candidatos o de tickets recientes",
            },
        },
        read_only=True,
    ),
    ToolSpec(
        "memory_stats",
        "Estadísticas e impacto de la memoria. mode=stats devuelve conteos "
        "por estado/scope/kind, presupuesto, propuestas, uso y routing; "
        "mode=impact devuelve tendencias de cierres, intentos, lead time y "
        "verificación en days; mode=all devuelve ambos. Solo lectura.",
        {
            "mode": {
                "type": "string",
                "enum": ["stats", "impact", "all"],
                "default": "all",
                "description": "stats, impact o ambos",
            },
            "days": {
                "type": "integer",
                "minimum": 1,
                "maximum": 3650,
                "default": 30,
                "description": "Ventana de impacto en días",
            },
        },
        read_only=True,
    ),
]
