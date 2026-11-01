"""Definiciones MCP: herramientas, recursos, prompts e instrucciones."""

from __future__ import annotations

from ticketlib.domain import KINDS, LINK_KINDS, PRIORITIES, STATUSES

INSTRUCTIONS = """\
Sistema de tickets local compartido entre agentes y el usuario.

Flujo de trabajo:
1. Ningún cambio sin ticket. Antes de editar o ejecutar algo que escriba, comprueba que tienes un ticket en "en_progreso": si no, créalo con taiki_create y reclámalo con taiki_claim. El guard de OpenCode bloquea la primera escritura y deja un ticket temporal S-#### reclamado (oculto, no consume números T-); si el trabajo es real, promuévelo con `taiki scratch --promote` o crea el ticket adecuado.
2. Al empezar, consulta taiki_list con status="pendiente" (o usa taiki_next) y revisa también "en_progreso" para no duplicar trabajo.
3. Si descubres trabajo nuevo, regístralo con taiki_create; no lo dejes solo en el chat. Para objetivos con varias tareas, crea una épica y las tareas con parent_id (T-0014.01, T-0014.02…).
4. Al terminar, usa taiki_complete con una resolución concreta y el commit o los archivos tocados.
5. Nunca muevas un ticket a "confirmado": esa transición la hace el usuario con el CLI.
6. Usa taiki_comment para decisiones o bloqueos y taiki_link para commits, archivos y URLs.
7. Incluye el código T-XXXX en el asunto del commit: el hook commit-msg lo exige.

Estados: nuevo → pendiente → en_progreso → completado → confirmado.
También existen "bloqueado" y "cancelado".
"""


def _tool(
    name: str,
    description: str,
    properties: dict,
    required: tuple[str, ...] = (),
) -> dict:
    return {
        "name": name,
        "description": description,
        "inputSchema": {
            "type": "object",
            "properties": properties,
            "required": list(required),
            "additionalProperties": False,
        },
    }


CODE = {
    "type": "string",
    "description": "Código del ticket, por ejemplo T-0001 o T-0014.2",
}
AGENT = {
    "type": "string",
    "description": "Nombre del agente que ejecuta la acción. Al crear, asigna el "
    "ticket a ese agente; en el resto de acciones identifica quién lo hizo",
}
STATUS = {
    "type": "string",
    "enum": list(STATUSES),
    "description": "Estado del ticket",
}

TOOLS = [
    _tool(
        "create",
        "Crea un ticket en estado 'nuevo'. Úsalo para registrar mantenimiento, "
        "funcionalidades, bugs, documentación o seguridad. Pasa siempre 'agent': "
        "el ticket queda asignado a ese agente y su sesión se registra sola.",
        {
            "title": {"type": "string", "description": "Título corto y accionable"},
            "body": {
                "type": "string",
                "description": "Contexto, criterios de aceptación, pasos para reproducir, etc.",
            },
            "kind": {
                "type": "string",
                "enum": list(KINDS),
                "description": "Tipo de trabajo",
                "default": "otro",
            },
            "priority": {
                "type": "string",
                "enum": list(PRIORITIES),
                "description": "Prioridad",
                "default": "media",
            },
            "project": {
                "type": "string",
                "description": "Proyecto o repositorio. Por defecto se deduce del directorio actual.",
            },
            "path": {
                "type": "string",
                "description": "Ruta relevante. Por defecto, el directorio actual.",
            },
            "tags": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Etiquetas libres",
            },
            "parent_id": {
                "type": "string",
                "description": "Código del ticket padre, si es una subtarea",
            },
            "agent": AGENT,
        },
        ("title",),
    ),
    _tool(
        "list",
        "Lista tickets con filtros y paginación. Sin filtros devuelve los no "
        "archivados, del código más reciente al más antiguo (order='priority' "
        "ordena por prioridad y antigüedad). Devuelve 'total' para paginar con "
        "'offset'.",
        {
            "status": STATUS,
            "statuses": {
                "type": "array",
                "items": {"type": "string", "enum": list(STATUSES)},
                "description": "Varios estados a la vez; tiene prioridad sobre 'status'",
            },
            "kind": {"type": "string", "enum": list(KINDS)},
            "priority": {"type": "string", "enum": list(PRIORITIES)},
            "project": {"type": "string", "description": "Filtrar por proyecto"},
            "assignee": {"type": "string", "description": "Filtrar por agente asignado"},
            "only_roots": {
                "type": "boolean",
                "default": False,
                "description": "Solo épicas y tickets sin padre",
            },
            "parent_id": {
                "type": "string",
                "description": "Solo las tareas de esta épica",
            },
            "tag": {
                "type": "string",
                "description": "Filtrar por etiqueta",
            },
            "tags": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Filtrar por una o más etiquetas",
            },
            "query": {
                "type": "string",
                "description": "Búsqueda de texto completo en título y descripción",
            },
            "include_archived": {"type": "boolean", "default": False},
            "limit": {
                "type": "integer",
                "minimum": 1,
                "maximum": 500,
                "default": 50,
            },
            "offset": {
                "type": "integer",
                "minimum": 0,
                "default": 0,
                "description": "Tickets a saltar para paginar",
            },
            "order": {
                "type": "string",
                "enum": ["code", "priority"],
                "default": "code",
                "description": "code = más recientes primero; priority = prioridad y antigüedad",
            },
        },
    ),
    _tool(
        "get",
        "Obtiene un ticket completo: datos, etiquetas, comentarios, enlaces e historial.",
        {"code": CODE},
        ("code",),
    ),
    _tool(
        "claim",
        "Reclama un ticket y lo pasa a 'en_progreso', asignándolo al agente. "
        "Llámalo antes de empezar a modificar código.",
        {"code": CODE, "agent": AGENT},
        ("code",),
    ),
    _tool(
        "update",
        "Actualiza campos descriptivos de un ticket. No cambia el estado.",
        {
            "code": CODE,
            "title": {"type": "string"},
            "body": {"type": "string"},
            "kind": {"type": "string", "enum": list(KINDS)},
            "priority": {"type": "string", "enum": list(PRIORITIES)},
            "project": {"type": "string"},
            "path": {"type": "string"},
            "tags": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Reemplaza el conjunto completo de etiquetas",
            },
            "parent_id": {
                "type": ["string", "null"],
                "description": "Épica de la tarea; null la devuelve a la raíz",
            },
            "agent": AGENT,
        },
        ("code",),
    ),
    _tool(
        "transition",
        "Cambia el estado de un ticket validando la máquina de estados. "
        "No permite pasar a 'confirmado' (reservado al usuario).",
        {
            "code": CODE,
            "status": STATUS,
            "note": {
                "type": "string",
                "description": "Motivo o contexto. Obligatorio al reabrir.",
            },
            "agent": AGENT,
        },
        ("code", "status"),
    ),
    _tool(
        "complete",
        "Marca un ticket como 'completado' con una resolución obligatoria. "
        "Adjunta commit y archivos tocados.",
        {
            "code": CODE,
            "resolution": {
                "type": "string",
                "description": "Qué se hizo, cómo se verificó y cualquier efecto secundario",
            },
            "commit": {"type": "string", "description": "Hash o referencia del commit"},
            "files": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Archivos modificados",
            },
            "agent": AGENT,
        },
        ("code", "resolution"),
    ),
    _tool(
        "comment",
        "Añade un comentario al historial del ticket (decisiones, bloqueos, hallazgos).",
        {"code": CODE, "body": {"type": "string"}, "agent": AGENT},
        ("code", "body"),
    ),
    _tool(
        "link",
        "Vincula una referencia externa al ticket.",
        {
            "code": CODE,
            "kind": {
                "type": "string",
                "enum": list(LINK_KINDS),
                "description": "Tipo de referencia",
            },
            "ref": {"type": "string", "description": "Commit, ruta, URL o id de sesión"},
            "agent": AGENT,
        },
        ("code", "kind", "ref"),
    ),
    _tool(
        "stats",
        "Resumen de tickets por estado, tipo, prioridad y proyecto.",
        {},
    ),
    _tool(
        "next",
        "Devuelve el siguiente ticket pendiente (o nuevo) por prioridad y antigüedad.",
        {"project": {"type": "string", "description": "Filtrar por proyecto"}},
    ),
]

RESOURCES = [
    {
        "uri": "taiki://inbox",
        "name": "Bandeja de entrada",
        "description": "Tickets en estado nuevo o pendiente",
        "mimeType": "application/json",
    },
    {
        "uri": "taiki://stats",
        "name": "Estadísticas",
        "description": "Conteos por estado, tipo, prioridad y proyecto",
        "mimeType": "application/json",
    },
]

RESOURCE_TEMPLATES = [
    {
        "uriTemplate": "taiki://ticket/{code}",
        "name": "Ticket",
        "description": "Detalle completo de un ticket por código (T-0001)",
        "mimeType": "application/json",
    }
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
        "description": "Lista tickets completados pendientes de confirmación del usuario",
        "arguments": [],
    },
]
