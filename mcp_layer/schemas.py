"""Definiciones MCP: herramientas, recursos, prompts e instrucciones."""

from __future__ import annotations

from dataclasses import dataclass

from ticketlib.domain import KINDS, LINK_KINDS, PRIORITIES, STATUSES

INSTRUCTIONS = """\
Sistema de tickets local compartido entre agentes y el usuario.

Flujo de trabajo:
1. Ningún cambio sin ticket. Antes de editar o ejecutar algo que escriba, comprueba que tienes un ticket en "en_progreso": si no, créalo con create y reclámalo con claim. En clientes que prefijan el servidor verás taiki_create/taiki_claim; el nombre real es create/claim. El guard de OpenCode puede crear un ticket temporal S-#### oculto; si el trabajo es real, crea el ticket adecuado.
2. Al empezar, usa list (por defecto solo abiertos y en resumen) o next, y revisa también "en_progreso" para no duplicar trabajo. Para el detalle de un ticket usa get (include y history_limit acotan secciones). Si hay otra sesión activa, consulta status y whoami; reclama con worktree=true para aislar la escritura y usa heartbeat en comandos largos.
   Antes de paralelizar usa schedule (tareas listas y disjuntas). Durante el trabajo publica checkpoint (qué hiciste y qué falta); si un worker cae, reassign reasigna leases caducados y retry reintenta en la misma sesión hasta TAIKI_MAX_ATTEMPTS (3 por defecto).
3. Si descubres trabajo nuevo, regístralo con create; no lo dejes solo en el chat. Para objetivos con varias tareas, crea una épica y las tareas con parent_id (T-0014.01, T-0014.02…). Declara las rutas que posee con paths.
4. Pasa agent cuando puedas; si lo omites se usa el nombre del cliente. No reclames una épica: reclama una de sus tareas.
5. Al terminar, usa complete con una resolución concreta y el commit o los archivos tocados.
6. Nunca muevas un ticket a "confirmado": esa transición la hace el usuario con el CLI.
7. Usa comment para decisiones o bloqueos y link para commits, archivos y URLs. Incluye el código T-XXXX en el asunto del commit: el hook commit-msg lo exige.

Estados: nuevo → pendiente → en_progreso → completado → verificado → confirmado.
"verificado" lo marca el conductor con evidencia (tests/CI/revisión) y sigue
pendiente de la confirmación del usuario; "confirmado" es exclusivo del usuario.
También existen "bloqueado" y "cancelado".
"""


@dataclass(frozen=True)
class ToolSpec:
    """Fuente única de una herramienta MCP.

    El esquema que ve el cliente, el despacho y el conjunto de herramientas que
    escriben se derivan de esta definición y del nombre ``_handle_<name>``.
    """

    name: str
    description: str
    properties: dict
    required: tuple[str, ...] = ()
    read_only: bool = False

    def schema(self) -> dict:
        tool = {
            "name": self.name,
            "description": self.description,
            "inputSchema": {
                "type": "object",
                "properties": self.properties,
                "required": list(self.required),
                "additionalProperties": False,
            },
        }
        # Solo se anota lo que aporta una pista real al cliente; títulos e
        # ``idempotentHint``/``destructiveHint`` duplicaban el catálogo sin uso.
        if self.read_only:
            tool["annotations"] = {"readOnlyHint": True}
        return tool


CODE = {
    "type": "string",
    "maxLength": 40,
    "description": "Código del ticket, por ejemplo T-0001 o T-0014.2",
}
AGENT = {
    "type": "string",
    "maxLength": 80,
    "description": "Nombre del agente que ejecuta la acción. Al crear, asigna el "
    "ticket a ese agente; en el resto de acciones identifica quién lo hizo",
}
STATUS = {
    "type": "string",
    "enum": list(STATUSES),
    "description": "Estado del ticket",
}
SESSION = {
    "type": "string",
    "description": "Identificador de sesión. Solo si tu cliente no lo envía en _meta.",
}
FORCE = {
    "type": "boolean",
    "default": False,
    "description": "Fuerza la operación aunque el ticket esté reclamado por otra sesión.",
}
DETAIL = {
    "type": "boolean",
    "default": False,
    "description": "Devuelve el ticket completo en vez del recibo compacto",
}

TOOL_SPECS = [
    ToolSpec(
        "create",
        "Crea un ticket en estado 'nuevo'. Úsalo para registrar mantenimiento, "
        "funcionalidades, bugs, documentación o seguridad. Pasa 'agent' o se "
        "usará el nombre del cliente MCP; el ticket queda asignado y su sesión "
        "se registra sola. Con subtasks crea las hijas en la misma llamada y "
        "con idempotency_key los reintentos devuelven el mismo ticket.",
        {
            "title": {
                "type": "string",
                "maxLength": 200,
                "description": "Título corto y accionable",
            },
            "body": {
                "type": "string",
                "maxLength": 100_000,
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
                "maxLength": 200,
                "description": "Proyecto o repositorio. Por defecto se deduce del directorio actual.",
            },
            "path": {
                "type": "string",
                "maxLength": 2_000,
                "description": "Ruta relevante. Por defecto, el directorio actual.",
            },
            "paths": {
                "type": "array",
                "items": {"type": "string", "maxLength": 2_000},
                "maxItems": 200,
                "description": "Rutas o globs que posee el ticket",
            },
            "tags": {
                "type": "array",
                "items": {"type": "string", "maxLength": 80},
                "maxItems": 50,
                "description": "Etiquetas libres",
            },
            "parent_id": {
                "type": "string",
                "maxLength": 40,
                "description": "Código del ticket padre, si es una subtarea",
            },
            "subtasks": {
                "type": "array",
                "items": {"type": "string", "maxLength": 200},
                "maxItems": 100,
                "description": "Títulos de las tareas hijas a crear con el ticket",
            },
            "idempotency_key": {
                "type": "string",
                "maxLength": 200,
                "description": "Clave para que un reintento devuelva el mismo ticket",
            },
            "detail": DETAIL,
            "agent": AGENT,
        },
        ("title",),
    ),
    ToolSpec(
        "list",
        "Lista tickets con filtros y paginación. Sin filtros devuelve solo los "
        "tickets abiertos, en resumen lean y 20 por defecto, del código más "
        "reciente al más antiguo. Usa include_closed=true para ver también "
        "cerrados, detail=true para el registro completo y get para un ticket "
        "concreto. Devuelve 'total' para paginar con 'offset'.",
        {
            "status": STATUS,
            "statuses": {
                "type": "array",
                "items": {"type": "string", "enum": list(STATUSES)},
                "maxItems": 10,
                "description": "Varios estados a la vez; tiene prioridad sobre 'status'",
            },
            "kind": {"type": "string", "enum": list(KINDS)},
            "priority": {"type": "string", "enum": list(PRIORITIES)},
            "project": {
                "type": "string",
                "maxLength": 200,
                "description": "Filtrar por proyecto",
            },
            "assignee": {
                "type": "string",
                "maxLength": 80,
                "description": "Filtrar por agente asignado",
            },
            "only_roots": {
                "type": "boolean",
                "default": False,
                "description": "Solo épicas y tickets sin padre",
            },
            "leaves_only": {
                "type": "boolean",
                "default": False,
                "description": "Excluye épicas que ya tienen tareas",
            },
            "ready": {
                "type": "boolean",
                "default": False,
                "description": "Solo tareas sin dependencias abiertas",
            },
            "parent_id": {
                "type": "string",
                "maxLength": 40,
                "description": "Solo las tareas de esta épica",
            },
            "tag": {
                "type": "string",
                "maxLength": 80,
                "description": "Filtrar por etiqueta",
            },
            "tags": {
                "type": "array",
                "items": {"type": "string", "maxLength": 80},
                "maxItems": 50,
                "description": "Filtrar por una o más etiquetas",
            },
            "query": {
                "type": "string",
                "maxLength": 500,
                "description": "Búsqueda de texto completo en título y descripción",
            },
            "include_archived": {"type": "boolean", "default": False},
            "include_closed": {
                "type": "boolean",
                "default": False,
                "description": "Incluye confirmados y cancelados; por defecto solo abiertos",
            },
            "detail": {
                "type": "boolean",
                "default": False,
                "description": "Devuelve el registro completo en vez del resumen",
            },
            "fields": {
                "type": "array",
                "items": {"type": "string", "maxLength": 40},
                "maxItems": 50,
                "description": "Lista explícita de campos a devolver (incluye body si se pide)",
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
                "description": "Tickets a saltar para paginar",
            },
            "order": {
                "type": "string",
                "enum": ["code", "priority"],
                "default": "code",
                "description": "code = más recientes primero; priority = prioridad y antigüedad",
            },
        },
        read_only=True,
    ),
    ToolSpec(
        "get",
        "Obtiene un ticket completo: datos, etiquetas, comentarios, enlaces e "
        "historial. Usa include para pedir secciones y history_limit/"
        "links_limit/subtasks_limit/commits_limit para acotarlas; "
        "events_detail=true recupera todos los campos de los eventos. "
        "fields proyecta solo los campos pedidos. "
        "raw=true devuelve el registro íntegro, sin proyecciones ni límites.",
        {
            "code": CODE,
            "raw": {
                "type": "boolean",
                "default": False,
                "description": "Devuelve el ticket íntegro: ignora include, "
                "events_detail y los límites de secciones",
            },
            "fields": {
                "type": "array",
                "items": {"type": "string", "maxLength": 60},
                "maxItems": 40,
                "description": "Proyecta solo estos campos del ticket "
                "(code se incluye siempre)",
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
                "description": "Secciones a incluir; si se omite, se incluyen todas",
            },
            "history_limit": {
                "type": "integer",
                "minimum": 0,
                "maximum": 500,
                "default": 15,
                "description": "Máximo de eventos y comentarios (0 = sin límite)",
            },
            "links_limit": {
                "type": "integer",
                "minimum": 0,
                "maximum": 500,
                "default": 20,
                "description": "Máximo de enlaces (0 = sin límite)",
            },
            "subtasks_limit": {
                "type": "integer",
                "minimum": 0,
                "maximum": 500,
                "default": 20,
                "description": "Máximo de subtareas (0 = sin límite)",
            },
            "commits_limit": {
                "type": "integer",
                "minimum": 0,
                "maximum": 500,
                "default": 20,
                "description": "Máximo de commits enlazados (0 = sin límite)",
            },
            "events_detail": {
                "type": "boolean",
                "default": False,
                "description": "Incluye todos los campos de cada evento; por defecto solo ts/actor/action/note",
            },
        },
        ("code",),
        read_only=True,
    ),
    ToolSpec(
        "claim",
        "Reclama un ticket y lo pasa a 'en_progreso', asignándolo al agente. "
        "Llámalo antes de empezar a modificar código. No reclames una épica: "
        "reclama una de sus tareas.",
        {
            "code": CODE,
            "agent": AGENT,
            "session_id": SESSION,
            "steal": {
                "type": "boolean",
                "description": "Recupera un claim caducado de otra sesión",
                "default": False,
            },
            "force": FORCE,
            "worktree": {
                "type": "boolean",
                "default": False,
                "description": "Crea y enlaza un worktree para el ticket",
            },
            "lease_minutes": {
                "type": "integer",
                "minimum": 1,
                "maximum": 1440,
                "description": "Minutos de lease (por defecto TAIKI_LEASE_MINUTES)",
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
            "tags": {
                "type": "array",
                "items": {"type": "string", "maxLength": 80},
                "maxItems": 50,
                "description": "Reemplaza el conjunto completo de etiquetas",
            },
            "parent_id": {
                "type": ["string", "null"],
                "maxLength": 40,
                "description": "Épica de la tarea; null la devuelve a la raíz",
            },
            "assigned_to": {
                "type": ["string", "null"],
                "maxLength": 80,
                "description": "Reasigna el ticket; null lo deja sin agente",
            },
            "detail": DETAIL,
            "agent": AGENT,
            "session_id": SESSION,
        },
        ("code",),
    ),
    ToolSpec(
        "transition",
        "Cambia el estado de un ticket validando la máquina de estados. "
        "Pasar a 'verificado' exige evidencia en note. "
        "No permite pasar a 'confirmado' (reservado al usuario).",
        {
            "code": CODE,
            "status": STATUS,
            "note": {
                "type": "string",
                "maxLength": 10_000,
                "description": "Motivo o contexto. Obligatorio al reabrir.",
            },
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
                "type": "string",
                "maxLength": 100_000,
                "description": "Qué se hizo, cómo se verificó y cualquier efecto secundario",
            },
            "commit": {
                "type": "string",
                "maxLength": 200,
                "description": "Hash o referencia del commit",
            },
            "files": {
                "type": "array",
                "items": {"type": "string", "maxLength": 2_000},
                "maxItems": 500,
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
        "comment",
        "Añade un comentario al historial del ticket (decisiones, bloqueos, hallazgos).",
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
                "type": "string",
                "enum": list(LINK_KINDS),
                "description": "Tipo de referencia",
            },
            "ref": {
                "type": "string",
                "maxLength": 2_000,
                "description": "Commit, ruta, URL o id de sesión",
            },
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
        "Devuelve el siguiente ticket pendiente (o nuevo) por prioridad y "
        "antigüedad, como resumen con un avance del body. Usa detail=true para "
        "el registro completo.",
        {
            "project": {"type": "string", "description": "Filtrar por proyecto"},
            "detail": {
                "type": "boolean",
                "default": False,
                "description": "Devuelve el ticket completo en vez del resumen",
            },
            "ready": {
                "type": "boolean",
                "default": True,
                "description": "Salta tareas con dependencias abiertas",
            },
        },
        read_only=True,
    ),
    ToolSpec(
        "agents",
        "Registro de capacidades de agentes: modelo, variante, fortalezas por "
        "tipo, paralelismo máximo, rutas y coste. Con kind devuelve además el "
        "agente recomendado para ese tipo de trabajo.",
        {
            "kind": {
                "type": "string",
                "enum": list(KINDS),
                "description": "Tipo de trabajo para recomendar un agente",
            },
        },
        read_only=True,
    ),
    ToolSpec(
        "status",
        "Foto del proyecto: sesiones activas, claims, leases, worktrees, "
        "solapamientos y servidores MCP. Úsalo antes de escribir si hay otros "
        "agentes.",
        {
            "window": {
                "type": "integer",
                "minimum": 60,
                "maximum": 86400,
                "default": 600,
                "description": "Ventana de actividad reciente en segundos",
            },
            "servers": {
                "type": "boolean",
                "default": False,
                "description": "Incluye el inventario de servidores MCP",
            },
        },
        read_only=True,
    ),
    ToolSpec(
        "whoami",
        "Devuelve tu agente y sesión resueltos, el ticket activo, su lease y "
        "su worktree. Úsalo para comprobar quién eres antes de coordinar.",
        {},
        read_only=True,
    ),
    ToolSpec(
        "heartbeat",
        "Renueva el lease del ticket activo o del código indicado. Llámalo "
        "durante comandos largos para que otra sesión no lo tome por muerto.",
        {
            "code": CODE,
            "minutes": {
                "type": "integer",
                "minimum": 1,
                "maximum": 1440,
                "description": "Minutos de lease (por defecto TAIKI_LEASE_MINUTES)",
            },
        },
    ),
    ToolSpec(
        "checkpoint",
        "Publica el progreso del worker (qué hizo y qué falta) y renueva el "
        "lease del ticket.",
        {
            "code": CODE,
            "note": {
                "type": "string",
                "maxLength": 10_000,
                "description": "Qué se hizo desde el último checkpoint",
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
        "Reintenta un fallo en la misma sesión o bloquea el ticket al agotar "
        "los intentos. No reabre un ticket bloqueado: eso requiere decisión "
        "humana.",
        {
            "code": CODE,
            "why": {
                "type": "string",
                "maxLength": 10_000,
                "description": "Motivo del fallo que se reintenta",
            },
            "limit": {
                "type": "integer",
                "minimum": 1,
                "maximum": 20,
                "description": "Máximo de intentos (por defecto TAIKI_MAX_ATTEMPTS o 3)",
            },
            "agent": AGENT,
            "session_id": SESSION,
        },
        ("code", "why"),
    ),
    ToolSpec(
        "schedule",
        "Plan de paralelismo: tareas listas y disjuntas que caben en el "
        "presupuesto; las que solapan o tocan zonas seriales se ejecutan de "
        "una en una.",
        {
            "project": {
                "type": "string",
                "maxLength": 200,
                "description": "Filtrar por proyecto",
            },
            "limit": {
                "type": "integer",
                "minimum": 1,
                "maximum": 8,
                "description": "Writers simultáneos (por defecto orchestrator.max_parallel)",
            },
        },
        read_only=True,
    ),
    ToolSpec(
        "reassign",
        "Reasigna los tickets cuyo lease caducó (workers caídos o colgados) al "
        "agente y sesión indicados.",
        {
            "agent": AGENT,
            "session_id": SESSION,
        },
    ),
    ToolSpec(
        "worktree_create",
        "Crea (o reutiliza) el worktree del ticket reclamado y lo enlaza. "
        "Trabaja dentro de la ruta devuelta para no pisar el árbol compartido.",
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
        "promote",
        "Promueve un ticket scratch S-#### a un ticket real T-####. Úsalo "
        "cuando el trabajo temporal del guard resulta ser real.",
        {"code": CODE},
        ("code",),
    ),
    ToolSpec(
        "scratch_drop",
        "Descarta un ticket scratch S-####: lo archiva y lo deja cancelado, "
        "sin pasar por la tabla ni consumir números T-####.",
        {"code": CODE},
        ("code",),
    ),
    ToolSpec(
        "health",
        "Comprueba el estado del MCP, hooks, claims caducados, solapamientos, "
        "worktrees y servidores. Úsalo si algo no cuadra.",
        {
            "days": {
                "type": "integer",
                "minimum": 1,
                "maximum": 365,
                "default": 2,
                "description": "Días para detectar tickets estancados",
            },
            "servers": {
                "type": "boolean",
                "default": True,
                "description": "Incluye el resumen de servidores MCP",
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
]

TOOLS = [spec.schema() for spec in TOOL_SPECS]

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
        "description": "Lista tickets completados o verificados pendientes de confirmación del usuario",
        "arguments": [],
    },
]
