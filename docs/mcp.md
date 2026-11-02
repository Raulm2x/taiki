# MCP

Volver a [README](../README.md) · [Índice de documentación](README.md)

## Herramientas MCP

El servidor expone 28 herramientas. El nombre real no lleva prefijo (`list`,
`claim`…), aunque algunos clientes las muestren como `taiki_*`.

| Herramienta | Uso |
|---|---|
| `list` | Abiertos en resumen; `include_closed`, `detail`, `fields`, `leaves_only` |
| `get` | Detalle; `include`, `fields`, `history_limit` y `raw` ajustan qué llega |
| `next` | Siguiente ticket en resumen; `detail=true` para el registro completo |
| `create`, `update`, `transition`, `complete`, `comment`, `link` | Ciclo de vida |
| `claim` | Reclama; `worktree=true`, `lease_minutes`, `steal`, `force` |
| `heartbeat` | Renueva el lease en comandos largos |
| `status`, `whoami` | Sesiones, claims, worktrees y tu ticket activo |
| `worktree_create`, `worktree_list` | Aislan la escritura por tarea |
| `promote` | Convierte un scratch `S-####` en `T-####` |
| `scratch_drop` | Archiva y cancela un scratch sin consumir `T-####` |
| `health` | MCP, hooks, claims caducados, solapamientos y worktrees |
| `brief` | Brief operativo de memoria; `section`, `budget` y `write` |
| `memory_search` | Busca memorias por texto, scope, tipo, estado, rutas o kinds; snippets salvo `include_content=true` |
| `memory_write` | Operaciones `add`, `patch`, `remove`, `supersede` y `feedback` con evidencia y aprobación humana |
| `memory_candidates` | Solo lectura: `mode=list` lista candidatos; `mode=review` devuelve el paquete del curator. Acepta `since`, `kind` y `limit` |

Los errores llegan como `{code, message, hint, retryable}` y las herramientas
de lectura llevan `readOnlyHint`. Si el código del MCP cambia, las escrituras
se bloquean hasta recargarlo: evita datos inconsistentes.

Envelopes: `get` devuelve el ticket en primer nivel, `next` devuelve
`{ticket}` y `list` devuelve `{tickets, count, total, offset, limit}`. Las
secciones de `get` se acotan con `history_limit`, `links_limit`,
`subtasks_limit` y `commits_limit`; `0` significa sin límite. `raw=true`
devuelve el registro íntegro e ignora esas proyecciones.

Los worktrees comparten la DB del checkout principal. Aunque el repositorio
versione `.taiki/config.json`, `taiki` resuelve el `.taiki` principal desde
cualquier worktree; no se crea una DB vacía dentro de `.worktrees/`.

## Recursos y prompts MCP

El catálogo de 28 herramientas está en [Herramientas MCP](#herramientas-mcp) y
se genera desde `mcp_layer/schemas.py`. Aquí solo quedan los recursos y prompts:

Recursos: `taiki://inbox`, `taiki://stats`, `taiki://ticket/{code}`,
`taiki://brief`, `taiki://memory/{code}` y `taiki://memory/candidates`.
Prompts: `taiki:next`, `taiki:review`, `taiki:brief` y `taiki:curate`.

Resolución de sesiones automática compatible con `sessionId`, `session_id`, `AGY_SESSION_ID` y `ai.opencode/sessionID`.

## Flujo recomendado para agentes

1. Al empezar: aplica `brief` (o el recurso `taiki://brief`) y usa
   `memory_search` para el contexto que falte; después `list` con
   `statuses=["pendiente"]` o `next`.
2. Antes de tocar código: `claim`. Si no hay ticket, créalo primero: el guard
   (cuando el proyecto lo tiene activado) bloquea la primera escritura y deja
   un ticket automático reclamado. Si el recibo trae `hints`, úsalos como
   contexto.
3. Trabajo nuevo descubierto: `create` **pasando `agent`**; el ticket queda
   asignado desde su creación.
4. Al terminar: `complete` con resolución y commit. Si hubo corrección, rework
   o un workaround no obvio, propón una lección con `memory_write` y evidencia;
   el recibo puede traer `learning`.
5. Nunca `confirmado`: eso lo hace el usuario con `taiki confirm`. Tampoco
   apruebes propuestas: `taiki memory approve` es del usuario.
6. Cada 5 tickets verificados/cerrados o al acumular 3+ candidatos, usa
   `memory_candidates(mode=review)` y el prompt `taiki:curate`. El curator
   propone como máximo 5 memorias, prefiere `patch`/`supersede`, exige
   evidencia y no aprueba ni edita código. El conductor lo lanza en background
   y publica las propuestas para el usuario.

En clientes que prefijan el servidor verás `taiki_list`, `taiki_claim`, etc.;
el nombre real de la herramienta es el que aparece en `tools/list`.

### Coste de contexto

- `list` devuelve 20 abiertos en resumen; usa `limit`/`offset`, `fields` o
  `detail` solo si necesitas más.
- `get` acota historial (15), enlaces (20) y subtareas (20) en SQL; `events_detail`
  y los límites a `0` amplían el detalle, `fields` proyecta columnas y
  `raw=true` devuelve el registro íntegro (incluye el note del cierre y los
  enlaces commit).
- `create`, `claim`, `update`, `transition`, `complete`, `comment` y `link`
  devuelven un recibo; `detail=true` recupera el ticket completo.
- En CLI, prefiere `taiki list --json-summary --compact --limit 20` y
  `taiki show T-XXXX --json --compact`; evita `list --json` completo.
  `--json-summary` no calcula sesiones ni `hidden_children` (solo el resumen
  lean).
- `taiki://inbox` devuelve como máximo 20 tickets en resumen.
- `brief` respeta `brief_budget_chars` (4500 por defecto) y omite lo que no
  cabe; `memory_search` devuelve snippets de 240 caracteres salvo
  `include_content=true`.
