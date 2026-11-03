"""Piezas comunes del catálogo MCP: instrucciones, ToolSpec y campos."""

from __future__ import annotations

from dataclasses import dataclass

from ticketlib.domain import STATUSES

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

Memoria:
- Al empezar aplica el brief (tool brief o recurso taiki://brief) y busca contexto con memory_search.
- Usa schedule y advice antes de reclamar: evita duplicar trabajo o pisar rutas.
- Al completar, si hubo corrección, rework o workaround, propón una lección con memory_write y evidencia (ticket, commit o archivo).
- No guardes fallos transitorios, secretos ni prohibiciones amplias; destila hechos y convenciones accionables.
- Las memorias propuestas no son instrucciones hasta que el usuario las apruebe.
- Si una memoria resulta incorrecta, usa patch, supersede o feedback en memory_write.
- Antes de cerrar una épica o al acumular >=3 candidatos, usa memory_candidates(mode=review) y propone lecciones; la aprobación es del usuario.
- Los playbooks se cargan bajo demanda con memory_playbook(slug) o el recurso taiki://playbook/{slug}; el brief solo muestra su índice.
- Antes de revisar un cambio, usa memory_checklist(kind="bug"|"funcionalidad", memory_kind="pitfall"|"convention", paths=[...]) para obtener pitfalls y convenciones acotados.

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
