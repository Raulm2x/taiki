"""Catálogo MCP de herramientas de tickets, orquestación y estado."""

from __future__ import annotations

from ticketlib.domain import (
    ISOLATION_MODES,
    KINDS,
    LINK_KINDS,
    PRIORITIES,
    STATUSES,
)
from ticketlib.store.workflow import EVIDENCE_KEYS

from .schema_common import (
    AGENT,
    CODE,
    DETAIL,
    FORCE,
    SESSION,
    STATUS,
    ToolSpec,
)

ISOLATION = {
    "type": "string",
    "enum": list(ISOLATION_MODES),
    "description": "none (serial), scope (paths+resources) o worktree (defecto)",
}
RESOURCES = {
    "type": "array",
    "items": {"type": "string", "maxLength": 200},
    "maxItems": 50,
    "description": "Recursos tipados: systemd:user/<unit>, package:<name>, …",
}
REVERSAL = {
    "type": ["object", "null"],
    "properties": {
        "kind": {"type": "string", "maxLength": 40},
        "ref": {"type": "string", "maxLength": 500},
        "command": {"type": "string", "maxLength": 2_000},
    },
    "required": ["kind"],
    "additionalProperties": False,
    "description": "Reversión {kind, ref, command}; null limpia",
}
EVIDENCE = {
    "type": "object",
    "properties": {
        key: {
            "type": "array",
            "items": {"type": "string", "maxLength": 200},
            "maxItems": 20,
        }
        for key in EVIDENCE_KEYS
    },
    "additionalProperties": False,
    "description": "Evidencia del verificado: {gates, commits, commands}",
}

TICKET_SPECS = [
    ToolSpec(
        "create",
        "Crea un ticket 'nuevo'; subtasks crea hijas, idempotency_key "
        "deduplica.",
        {
            "title": {"type": "string", "maxLength": 200, "description": "Título corto y accionable"},
            "body": {
                "type": "string", "maxLength": 100_000,
                "description": "Contexto y criterios",
            },
            "kind": {
                "type": "string", "enum": list(KINDS),
                "description": "Tipo de trabajo", "default": "otro",
            },
            "priority": {
                "type": "string", "enum": list(PRIORITIES),
                "description": "Prioridad", "default": "media",
            },
            "project": {
                "type": "string", "maxLength": 200,
                "description": "Proyecto (defecto: actual)",
            },
            "path": {
                "type": "string", "maxLength": 2_000,
                "description": "Ruta (defecto: actual)",
            },
            "paths": {
                "type": "array",
                "items": {"type": "string", "maxLength": 2_000},
                "maxItems": 200,
                "description": "Rutas o globs que posee el ticket",
            },
            "isolation": ISOLATION,
            "resources": RESOURCES,
            "reversal": REVERSAL,
            "tags": {
                "type": "array", "maxItems": 50,
                "items": {"type": "string", "maxLength": 80},
                "description": "Etiquetas libres",
            },
            "parent_id": {"type": "string", "maxLength": 40, "description": "Padre de la subtarea"},
            "subtasks": {
                "type": "array", "maxItems": 100,
                "items": {"type": "string", "maxLength": 200},
                "description": "Títulos de las hijas",
            },
            "idempotency_key": {"type": "string", "maxLength": 200, "description": "Deduplica reintentos"},
            "detail": DETAIL,
            "agent": AGENT,
        },
        ("title",),
    ),
    ToolSpec(
        "list",
        "Abiertos en resumen (20); include_closed añade cerrados; "
        "'total' pagina con offset.",
        {
            "status": STATUS,
            "statuses": {
                "type": "array",
                "items": {"type": "string", "enum": list(STATUSES)},
                "maxItems": 10,
                "description": "Estados varios",
            },
            "kind": {"type": "string", "enum": list(KINDS)},
            "priority": {"type": "string", "enum": list(PRIORITIES)},
            "project": {
                "type": "string",
                "maxLength": 200,
                "description": "Proyecto",
            },
            "assignee": {
                "type": "string",
                "maxLength": 80,
                "description": "Agente asignado",
            },
            "only_roots": {
                "type": "boolean",
                "default": False,
                "description": "Solo épicas y raíces",
            },
            "leaves_only": {
                "type": "boolean",
                "default": False,
                "description": "Excluye épicas con tareas",
            },
            "ready": {
                "type": "boolean",
                "default": False,
                "description": "Sin dependencias abiertas",
            },
            "parent_id": {
                "type": "string",
                "maxLength": 40,
                "description": "Tareas de esta épica",
            },
            "tag": {
                "type": "string",
                "maxLength": 80,
                "description": "Etiqueta",
            },
            "tags": {
                "type": "array",
                "items": {"type": "string", "maxLength": 80},
                "maxItems": 50,
                "description": "Etiquetas (todas)",
            },
            "query": {
                "type": "string",
                "maxLength": 500,
                "description": "Búsqueda de texto",
            },
            "include_archived": {"type": "boolean", "default": False},
            "include_closed": {
                "type": "boolean",
                "default": False,
                "description": "Incluye cerrados",
            },
            "detail": {
                "type": "boolean",
                "default": False,
                "description": "Registro completo",
            },
            "fields": {
                "type": "array",
                "items": {"type": "string", "maxLength": 40},
                "maxItems": 50,
                "description": "Campos a devolver",
            },
            "limit": {
                "type": "integer",
                "minimum": 1,
                "maximum": 500,
                "default": 20,
            },
            "offset": {
                "type": "integer",
                "minimum": 0,
                "default": 0,
                "description": "Salto de paginación",
            },
            "order": {
                "type": "string",
                "enum": ["code", "priority"],
                "default": "code",
                "description": "Orden code o priority",
            },
        },
        read_only=True,
    ),
    ToolSpec(
        "get",
        "Ticket completo por secciones; fields proyecta, raw=true lo da "
        "íntegro.",
        {
            "code": CODE,
            "raw": {
                "type": "boolean",
                "default": False,
                "description": "Ticket íntegro, sin proyecciones",
            },
            "fields": {
                "type": "array",
                "items": {"type": "string", "maxLength": 60},
                "maxItems": 40,
                "description": "Campos a devolver (code siempre)",
            },
            "include": {
                "type": "array",
                "items": {
                    "type": "string",
                    "enum": [
                        "events",
                        "comments",
                        "links",
                        "subtasks",
                        "commits",
                        "committed",
                    ],
                },
                "maxItems": 6,
                "description": "Secciones; sin valor, todas",
            },
            "history_limit": {
                "type": "integer",
                "minimum": 0,
                "maximum": 500,
                "default": 15,
                "description": "Tope de eventos y comentarios (0 = sin tope)",
            },
            "links_limit": {
                "type": "integer",
                "minimum": 0,
                "maximum": 500,
                "default": 20,
                "description": "Tope de enlaces (0 = sin tope)",
            },
            "subtasks_limit": {
                "type": "integer",
                "minimum": 0,
                "maximum": 500,
                "default": 20,
                "description": "Tope de subtareas (0 = sin tope)",
            },
            "commits_limit": {
                "type": "integer",
                "minimum": 0,
                "maximum": 500,
                "default": 20,
                "description": "Tope de commits (0 = sin tope)",
            },
            "events_detail": {
                "type": "boolean",
                "default": False,
                "description": "Campos completos del evento",
            },
        },
        ("code",),
        read_only=True,
    ),
    ToolSpec(
        "claim",
        "Reclama un ticket y pásalo a 'en_progreso' antes de escribir; "
        "nunca reclames una épica.",
        {
            "code": CODE,
            "agent": AGENT,
            "session_id": SESSION,
            "steal": {
                "type": "boolean",
                "description": "Recupera un claim caducado",
                "default": False,
            },
            "force": FORCE,
            "worktree": {
                "type": "boolean",
                "default": False,
                "description": "Crea y enlaza un worktree (solo aislamiento worktree)",
            },
            "lease_minutes": {
                "type": "integer",
                "minimum": 1,
                "maximum": 1440,
                "description": "Minutos de lease (defecto TAIKI_LEASE_MINUTES)",
            },
            "detail": DETAIL,
        },
        ("code",),
    ),
    ToolSpec(
        "update",
        "Actualiza campos descriptivos de un ticket. No cambia el estado.",
        {
            "code": CODE,
            "title": {"type": "string", "maxLength": 200},
            "body": {"type": "string", "maxLength": 100_000},
            "kind": {"type": "string", "enum": list(KINDS)},
            "priority": {"type": "string", "enum": list(PRIORITIES)},
            "project": {"type": "string", "maxLength": 200},
            "path": {"type": "string", "maxLength": 2_000},
            "paths": {
                "type": "array",
                "items": {"type": "string", "maxLength": 2_000},
                "maxItems": 200,
                "description": "Rutas o globs que posee el ticket",
            },
            "isolation": ISOLATION,
            "resources": RESOURCES,
            "reversal": REVERSAL,
            "tags": {
                "type": "array",
                "items": {"type": "string", "maxLength": 80},
                "maxItems": 50,
                "description": "Reemplaza todas las etiquetas",
            },
            "parent_id": {"type": ["string", "null"], "maxLength": 40, "description": "Épica; null la devuelve a la raíz"},
            "assigned_to": {"type": ["string", "null"], "maxLength": 80, "description": "Reasigna; null lo deja sin agente"},
            "detail": DETAIL,
            "agent": AGENT,
            "session_id": SESSION,
        },
        ("code",),
    ),
    ToolSpec(
        "transition",
        "Cambia el estado validando la máquina; 'verificado' exige "
        "evidencia y 'confirmado' es solo del usuario. Con el mismo "
        "estado solo documenta la resolución.",
        {
            "code": CODE,
            "status": STATUS,
            "note": {
                "type": "string", "maxLength": 10_000,
                "description": "Motivo; obligatorio al reabrir",
            },
            "resolution": {
                "type": "string", "maxLength": 100_000,
                "description": "Resolución; con el mismo estado no reabre",
            },
            "evidence": EVIDENCE,
            "agent": AGENT,
            "session_id": SESSION,
            "force": FORCE,
            "detail": DETAIL,
        },
        ("code", "status"),
    ),
    ToolSpec(
        "complete",
        "Marca un ticket como 'completado' con una resolución obligatoria. "
        "Adjunta commit y archivos tocados.",
        {
            "code": CODE,
            "resolution": {
                "type": "string", "maxLength": 100_000,
                "description": "Qué se hizo y cómo se verificó",
            },
            "commit": {"type": "string", "maxLength": 200, "description": "Hash o referencia del commit"},
            "files": {
                "type": "array", "maxItems": 500,
                "items": {"type": "string", "maxLength": 2_000},
                "description": "Archivos modificados",
            },
            "agent": AGENT,
            "session_id": SESSION,
            "force": FORCE,
            "detail": DETAIL,
        },
        ("code", "resolution"),
    ),
    ToolSpec(
        "reject_delivery",
        "Rechaza una entrega (evento delivery_rejected con reason "
        "obligatorio); reopen=true reabre a en_progreso.",
        {
            "code": CODE,
            "reason": {
                "type": "string", "maxLength": 10_000,
                "description": "Motivo; queda en el histórico",
            },
            "reopen": {
                "type": "boolean", "default": False,
                "description": "Reabre a en_progreso",
            },
            "agent": AGENT,
            "session_id": SESSION,
            "detail": DETAIL,
        },
        ("code", "reason"),
    ),
    ToolSpec(
        "comment",
        "Añade un comentario al historial (decisiones, bloqueos, hallazgos).",
        {
            "code": CODE,
            "body": {"type": "string", "maxLength": 50_000},
            "agent": AGENT,
            "session_id": SESSION,
            "detail": DETAIL,
        },
        ("code", "body"),
    ),
    ToolSpec(
        "link",
        "Vincula una referencia externa al ticket.",
        {
            "code": CODE,
            "kind": {
                "type": "string", "enum": list(LINK_KINDS),
                "description": "Tipo de referencia",
            },
            "ref": {"type": "string", "maxLength": 2_000, "description": "Commit, ruta, URL o id de sesión"},
            "agent": AGENT,
            "session_id": SESSION,
            "detail": DETAIL,
        },
        ("code", "kind", "ref"),
    ),
    ToolSpec(
        "stats",
        "Resumen de tickets por estado, tipo, prioridad y proyecto.",
        {},
        read_only=True,
    ),
    ToolSpec(
        "next",
        "Siguiente pendiente/nuevo por prioridad y antigüedad, en resumen; "
        "detail=true para el registro completo.",
        {
            "project": {"type": "string", "description": "Proyecto"},
            "detail": {
                "type": "boolean",
                "default": False,
                "description": "Ticket completo en vez de resumen",
            },
            "ready": {
                "type": "boolean",
                "default": True,
                "description": "Salta dependencias abiertas",
            },
        },
        read_only=True,
    ),
    ToolSpec(
        "agents",
        "Capacidades de agentes (modelo, fortalezas, paralelismo, rutas); "
        "con kind añade el recomendado.",
        {
            "kind": {
                "type": "string",
                "enum": list(KINDS),
                "description": "Tipo de trabajo",
            },
        },
        read_only=True,
    ),
    ToolSpec(
        "status",
        "Foto del proyecto: sesiones, claims, leases, worktrees y "
        "solapamientos; consúltalo antes de escribir en paralelo.",
        {
            "window": {
                "type": "integer",
                "minimum": 60,
                "maximum": 86400,
                "default": 600,
                "description": "Ventana de actividad (s)",
            },
            "servers": {
                "type": "boolean",
                "default": False,
                "description": "Incluye servidores MCP",
            },
        },
        read_only=True,
    ),
    ToolSpec(
        "whoami",
        "Tu agente y sesión resueltos, el ticket activo y su lease.",
        {},
        read_only=True,
    ),
    ToolSpec(
        "heartbeat",
        "Gestiona el lease: heartbeat renueva, abandon libera el claim y "
        "steps_exhausted registra el fin de pasos.",
        {
            "code": CODE,
            "action": {
                "type": "string",
                "enum": ["heartbeat", "abandon", "steps_exhausted"],
                "default": "heartbeat",
                "description": "Renueva, libera o registra pasos agotados",
            },
            "reason": {
                "type": "string", "maxLength": 10_000,
                "description": "Motivo; obligatorio en abandon con force",
            },
            "phase": {"type": "string", "maxLength": 40, "description": "Fase de agotamiento (defecto: estado actual)"},
            "force": FORCE,
            "minutes": {
                "type": "integer",
                "minimum": 1,
                "maximum": 1440,
                "description": "Minutos de lease (defecto TAIKI_LEASE_MINUTES)",
            },
        },
    ),
    ToolSpec(
        "checkpoint",
        "Publica el progreso del worker (hecho/pendiente) y renueva el lease.",
        {
            "code": CODE,
            "note": {
                "type": "string",
                "maxLength": 10_000,
                "description": "Hecho desde el último checkpoint",
            },
            "pending": {
                "type": "string",
                "maxLength": 10_000,
                "description": "Qué falta para terminar",
            },
            "agent": AGENT,
            "session_id": SESSION,
        },
        ("code", "note"),
    ),
    ToolSpec(
        "retry",
        "Reintenta en la misma sesión o bloquea al agotar intentos; no "
        "reabre un bloqueado.",
        {
            "code": CODE,
            "why": {
                "type": "string",
                "maxLength": 10_000,
                "description": "Motivo del fallo",
            },
            "limit": {
                "type": "integer",
                "minimum": 1,
                "maximum": 20,
                "description": "Tope de intentos (defecto TAIKI_MAX_ATTEMPTS o 3)",
            },
            "agent": AGENT,
            "session_id": SESSION,
        },
        ("code", "why"),
    ),
    ToolSpec(
        "schedule",
        "Plan de paralelismo: tareas listas y disjuntas dentro del "
        "presupuesto; el resto, en serie.",
        {
            "project": {
                "type": "string",
                "maxLength": 200,
                "description": "Proyecto",
            },
            "limit": {
                "type": "integer",
                "minimum": 1,
                "maximum": 8,
                "description": "Writers (defecto orchestrator.max_parallel)",
            },
        },
        read_only=True,
    ),
    ToolSpec(
        "reassign",
        "Reasigna los tickets con lease caducado (workers caídos) al agente "
        "y sesión indicados.",
        {
            "agent": AGENT,
            "session_id": SESSION,
        },
    ),
    ToolSpec(
        "worktree_create",
        "Crea o reutiliza el worktree del ticket (solo aislamiento worktree) "
        "y trabaja dentro de la ruta devuelta.",
        {
            "code": CODE,
            "title": {
                "type": "string",
                "maxLength": 200,
                "description": "Título para el slug de la rama",
            },
            "force": FORCE,
        },
        ("code",),
    ),
    ToolSpec(
        "worktree_list",
        "Lista los worktrees del proyecto, opcionalmente filtrados por ticket.",
        {"code": CODE},
        read_only=True,
    ),
    ToolSpec(
        "epic_check",
        "Estado derivado de una épica desde sus hijas (read-only): "
        "abierta/completa, recuento, bloqueos y camino de cierre.",
        {"code": CODE},
        ("code",),
        read_only=True,
    ),
    ToolSpec(
        "promote",
        "Promueve un scratch S-#### a un ticket real T-####.",
        {"code": CODE},
        ("code",),
    ),
    ToolSpec(
        "scratch_drop",
        "Descarta un scratch S-####: archiva y cancela sin consumir "
        "números T-####.",
        {"code": CODE},
        ("code",),
    ),
    ToolSpec(
        "health",
        "Salud del MCP: hooks, claims caducados, solapamientos, worktrees "
        "y servidores. Úsalo si algo no cuadra.",
        {
            "days": {
                "type": "integer", "minimum": 1, "maximum": 365, "default": 2,
                "description": "Días para detectar estancados",
            },
            "servers": {
                "type": "boolean", "default": True,
                "description": "Incluye servidores MCP",
            },
        },
        read_only=True,
    ),
]
