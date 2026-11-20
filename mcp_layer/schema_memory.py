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
        "Brief operativo de memoria acotado por presupuesto; aplícalo al "
        "empezar. write=true genera .taiki/generated/brief.md.",
        {
            "budget": {
                "type": "integer",
                "minimum": 1,
                "maximum": 100_000,
                "description": "Presupuesto de caracteres",
            },
            "section": {
                "type": "string",
                "enum": ["project", "user", "all"],
                "default": "all",
                "description": "Sección a renderizar",
            },
            "write": {
                "type": "boolean",
                "default": False,
                "description": "Genera .taiki/generated/brief.md",
            },
        },
        read_only=True,
    ),
    ToolSpec(
        "memory_search",
        "Busca memorias vigentes con filtros (scope, tipo, estado, rutas, "
        "kinds); include_content=true para el contenido completo.",
        {
            "query": {
                "type": "string",
                "maxLength": 500,
                "description": "Texto libre; sin query se listan por prioridad",
            },
            "scope": {
                "type": "string",
                "enum": list(MEMORY_SCOPES),
                "description": "Scope",
            },
            "kind": {
                "type": "string",
                "enum": list(MEMORY_KINDS),
                "description": "Tipo principal",
            },
            "status": {
                "type": "string",
                "enum": list(MEMORY_STATUSES),
                "description": "Estado (defecto: sin archivadas)",
            },
            "paths": {
                "type": "array",
                "items": {"type": "string", "maxLength": 500},
                "maxItems": 100,
                "description": "Rutas que debe tocar",
            },
            "kinds": {
                "type": "array",
                "items": {"type": "string", "enum": list(MEMORY_KINDS)},
                "maxItems": 20,
                "description": "Tipos secundarios",
            },
            "ticket_kinds": {
                "type": "array",
                "items": {"type": "string", "enum": list(KINDS)},
                "maxItems": 20,
                "description": "Tipos de ticket (sin etiqueta = general)",
            },
            "limit": {
                "type": "integer",
                "minimum": 1,
                "maximum": 100,
                "default": 10,
                "description": "Tope de resultados",
            },
            "include_content": {
                "type": "boolean",
                "default": False,
                "description": "Contenido completo en vez de snippet",
            },
        },
        read_only=True,
    ),
    ToolSpec(
        "memory_checklist",
        "Checklist de pitfalls y convenciones para revisar un cambio; "
        "kind filtra por tipo de ticket y paths por rutas.",
        {
            "kind": {
                "type": "string",
                "enum": list(KINDS),
                "description": "Tipo de ticket",
            },
            "memory_kind": {
                "type": "string",
                "enum": list(CHECKLIST_KINDS),
                "description": "pitfall o convention",
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
                "description": "Tope de entradas",
            },
        },
        read_only=True,
    ),
    ToolSpec(
        "memory_playbook",
        "Carga un playbook por slug; path lee un apoyo concreto. Solo lectura.",
        {
            "slug": {
                "type": "string",
                "maxLength": 64,
                "description": "Slug, p. ej. revisar-pr",
            },
            "path": {
                "type": "string",
                "maxLength": 500,
                "description": "Apoyo a cargar (references/, scripts/, …)",
            },
        },
        ("slug",),
        read_only=True,
    ),
    ToolSpec(
        "memory_write",
        "Escribe o mantiene memorias y playbooks; las propuestas esperan "
        "aprobación. Aporta evidence (ticket, commit, file, url o session); "
        "con kind=playbook usa key=playbook:<slug>.",
        {
            "target": {
                "type": "string",
                "enum": list(MEMORY_SCOPES),
                "default": "project",
                "description": "Scope de las nuevas",
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
                            "description": "Operación",
                        },
                        "code": {
                            "type": "string",
                            "maxLength": 40,
                            "description": "M-0001 de la memoria",
                        },
                        "kind": {
                            "type": "string",
                            "enum": list(MEMORY_KINDS),
                            "description": "Tipo principal",
                        },
                        "ticket_kinds": {
                            "type": "array",
                            "items": {"type": "string", "enum": list(KINDS)},
                            "maxItems": 20,
                            "description": "Tipos de ticket",
                        },
                        "tags": {
                            "type": "array",
                            "items": {"type": "string", "maxLength": 33},
                            "maxItems": 20,
                            "description": "Etiquetas de dominio",
                        },
                        "key": {
                            "type": "string",
                            "maxLength": 200,
                            "description": "Clave estable",
                        },
                        "title": {
                            "type": "string",
                            "maxLength": 600,
                            "description": "Título breve y accionable",
                        },
                        "content": {
                            "type": "string",
                            "maxLength": 12_000,
                            "description": "Contenido destilado",
                        },
                        "description": {
                            "type": "string",
                            "maxLength": 600,
                            "description": "Resumen (240 en playbooks)",
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
                                        "description": "Ruta interna del apoyo",
                                    },
                                    "content": {
                                        "type": "string",
                                        "maxLength": 12_000,
                                        "description": "Contenido del apoyo",
                                    },
                                },
                            },
                            "description": "Ficheros de apoyo",
                        },
                        "evidence": {
                            "type": "array",
                            "maxItems": 20,
                            "items": {"type": ["string", "object"]},
                            "description": "Referencias que la respaldan",
                        },
                        "reason": {
                            "type": "string",
                            "maxLength": 2_000,
                            "description": "Motivo del cambio",
                        },
                        "confidence": {
                            "type": "number",
                            "minimum": 0,
                            "maximum": 1,
                            "description": "Confianza entre 0 y 1",
                        },
                        "pin": {
                            "type": "boolean",
                            "description": "Fija o desfija en el brief",
                        },
                        "helpful": {
                            "type": "boolean",
                            "description": "true si la memoria ayudó",
                        },
                    },
                },
                "description": "Operaciones en orden",
            },
        },
        ("operations",),
    ),
    ToolSpec(
        "memory_candidates",
        "Candidatos de aprendizaje deterministas del histórico; mode=review "
        "devuelve el paquete completo para el curator.",
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
                "description": "Ventana temporal: 90d o fecha ISO",
            },
            "kind": {
                "type": "string",
                "enum": list(MEMORY_KINDS),
                "description": "Tipo de memoria sugerida",
            },
            "limit": {
                "type": "integer",
                "minimum": 1,
                "maximum": 100,
                "default": 20,
                "description": "Tope de candidatos",
            },
        },
        read_only=True,
    ),
    ToolSpec(
        "memory_stats",
        "Estadísticas de memoria: mode=stats da conteos y presupuesto; "
        "mode=impact, tendencias en days; mode=all, ambos.",
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
