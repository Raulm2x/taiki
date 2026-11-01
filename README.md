# Taiki

Taiki es un gestor local de tickets en SQLite para que los agentes de IA
registren y coordinen trabajo (mantenimiento, funcionalidades, bugs,
documentación, seguridad) y para que el usuario revise y confirme lo hecho.

- **Proyecto**: `~/Projects/taiki` (repo independiente; `~/.config/tickets` solo
  conserva un shim de compatibilidad).
- **Servidor MCP**: `taiki` (stdio). No hay servidor global: se instala por
  proyecto con `taiki mcp install`.
- **CLI**: `taiki` para uso humano.
- **Base de datos**: una por proyecto en `.taiki/taiki.db`. Sin base de datos
  global: usar Taiki es una decisión de cada proyecto.

## Contenido

- [Workspaces: `.taiki`](#workspaces-taiki)
- [Estados](#estados)
- [Estructura](#estructura)
- [Instalación](#instalación)
- [Herramientas MCP](#herramientas-mcp)
- [CLI](#cli)
- [Guard y worktrees](#guard-ningún-cambio-sin-ticket)
- [Datos y respaldo](#datos-y-respaldo)
- [Desarrollo](#desarrollo)

## Workspaces: `.taiki`

Cada proyecto que quiera usar Taiki crea un directorio `.taiki/` en su raíz:

```text
mi-proyecto/
└── .taiki/
    ├── taiki.db          # SQLite del proyecto (ignorada por Git)
    ├── config.json       # configuración versionable
    ├── local.json        # ajustes de esta máquina (ignorado)
    └── .gitignore
```

La base de datos se resuelve, por orden:

1. `TAIKI_DB` o el alias histórico `TICKETS_DB` (override explícito).
2. El `.taiki/` más cercano subiendo desde el directorio actual. La búsqueda se
   detiene antes de `$HOME` para no heredar un `~/.taiki` en todos los proyectos.
3. Si no hay ninguno, error claro con `taiki init --workspace`.

`config.json`:

```json
{
  "version": 1,
  "project": "mi-proyecto",
  "name": "Mi proyecto",
  "guard": false,
  "guard_auto": "scratch",
  "defaults": {"kind": "otro", "priority": "media"},
  "ui": {"scope": "atencion", "refresh": 2.0}
}
```

- `guard` es opt-in: `taiki mcp install` lo activa; `taiki mcp uninstall` lo
  desactiva.
- `guard_auto` decide qué ticket crea el guard: `scratch` (por defecto, solo
  temporales `S-####`), `auto` (tarea visible si la sesión tiene épica abierta),
  `ticket` (siempre visible) u `off` (bloquear sin crear). `TAIKI_GUARD_AUTO`
  tiene prioridad sobre este valor.
- `local.json` permite sobrescribir ajustes que no deben viajar en Git.

## Estados

```
nuevo → pendiente → en_progreso → completado → verificado → confirmado
                 ↘ bloqueado ↗                    ↘ en_progreso (rechazo)
                 ↘ cancelado ↗ (reabrible)
```

- `completado`: el worker terminó (commit, tests y criterios de aceptación).
- `verificado`: el conductor revisó y ejecutó los gates; exige evidencia en la
  nota (tests, CI, revisión). No cuenta como abierto ni como final.
- `confirmado` solo lo puede aplicar el usuario desde el CLI, tanto desde
  `completado` como desde `verificado`.
- Reabrir un ticket (`completado`, `verificado`, `confirmado` o `cancelado`)
  exige un motivo e incrementa el contador de intentos.
- Cada cambio queda registrado en el historial con actor, sesión y nota.

## Orquestación

Taiki incluye un conductor primario y los subagentes `worker`, `worker-low`,
`worker-medium`, `worker-high` y `reviewer` en `.opencode/agents/`. El conductor planifica, delega, verifica con
`python3 tests/run.py --verify` y marca `verificado`; solo el usuario confirma
y mergea. Las capacidades (tipos, variantes, paralelismo y coste) viven en
`.taiki/config.json` y se consultan con la herramienta MCP `agents`.

Routing de modelos: `documentacion` → `low`; mantenimiento/funcionalidad →
`medium`; `bug`/`seguridad` → `high`. Todos los perfiles worker usan
`opencode-go/deepseek-v4.1-flash` para no disparar el coste en tokens;
`worker-medium` va sin variante porque deepseek no expone `medium`, así que se
usa el default del cliente. Si el modelo no expone la variante, se usa el
default; nunca se escala en bucle. El límite de writers
simultáneos es `orchestrator.max_parallel` (3 por defecto). Antes de
paralelizar, la herramienta MCP `schedule` separa las tareas en `parallel`
(paths disjuntos) y `serial` (solapes, esquema, store core, config o sin
paths); las migraciones y los merges nunca van en paralelo.

`taiki status` (y el modal `S` de la TUI) muestra la cola del orquestador
(listos, en curso, verificando, bloqueados y por confirmar), los worktrees y
métricas: lead time, intentos medios, fallos de verificación y paralelismo
máximo observado. «Por confirmar» suma `completado` y `verificado`;
«verificando» es el subconjunto que el conductor ya validó. El coste y los
tokens quedan como `no disponible` mientras no haya telemetría del proveedor.

Una tarea con dependencias no está lista hasta que el prerrequisito está
`verificado` (o `confirmado`/`cancelado`): `completado` aún puede fallar la
verificación y no desbloquea trabajo derivado.

## Estructura

```
taiki/
├── server.py              entrada MCP (transporte stdio JSON-RPC)
├── cli.py                 fachada del CLI (compatibilidad y `bin/taiki`)
├── parser.py              argumentos y `main()`
├── commands/              un módulo por área de comandos
│   ├── common.py          conexión, salida y lectura de cuerpo
│   ├── tickets.py         list, show, new, update, claim, next, stats, export
│   ├── workflow.py        transition, confirm, reopen, complete, comment
│   ├── git.py             link, auto-link, hook-install, audit
│   ├── guard.py           taiki guard (¿hay ticket activo para esta acción?)
│   ├── mcp.py             instala/retira el MCP solo en el proyecto actual
│   ├── servers.py         taiki servers (inventario y limpieza de MCP)
│   ├── worktree.py        taiki worktree (aislamiento por tarea)
│   ├── commit.py          taiki commit y hook-check-index
│   ├── status.py          taiki status (sesiones y claims)
│   ├── heartbeat.py       taiki heartbeat (renueva el lease)
│   └── system.py          open, scheme-install, scheme-uninstall, tui
├── tui/                   TUI en curses
│   ├── state.py           estado, filtros y geometría (sin curses)
│   ├── detail.py          panel de detalle y feed de actividad
│   ├── data.py            sondeo incremental y snapshots
│   ├── actions.py         acciones de administración (sin curses)
│   ├── actions_ui.py      flujos interactivos de acciones
│   ├── view.py            dibujo, paleta, menús y modales
│   └── app.py             bucle principal, atajos y ratón
├── tests/                 suite modular (una DB temporal por módulo)
│   ├── run.py             runner stdlib: --list, --only, -k, --failfast, --audit
│   ├── support.py         Harness, aislamiento de entorno y aserciones
│   ├── seeds.py           datasets y repositorios de partida
│   ├── hygiene.py         auditoría, presupuesto y cuarentena
│   ├── budget.toml        límites de tiempo y línea base de comprobaciones
│   ├── quarantine.toml    módulos aparcados con dueño y caducidad
│   ├── test_core.py       ciclo de vida, commits y listado
│   ├── test_tags_scope.py filtros por etiqueta y resolución de ámbito
│   ├── test_store.py      concurrencia, transacciones y subtareas
│   ├── test_mcp.py        handlers MCP, sesiones y código desactualizado
│   ├── test_sessions.py   sesiones de agente, enlaces y terminales
│   ├── test_cli.py        confirmación por lotes y acciones de administración
│   ├── test_tui_state.py  estado, ámbitos, filtros, novedades, mapa y FTS
│   ├── test_tui_render.py mapa, panel de detalle y dibujo
│   ├── test_hierarchy.py  épicas, tareas y renumeración
│   ├── test_guard.py      guard de escritura y tickets scratch
│   ├── test_claims.py     claims por sesión, leases y --steal
│   ├── test_worktrees.py  worktrees, bootstrap y resolución de DB
│   ├── test_commit.py     commit por rutas y solapamientos
│   ├── test_status.py     estado de sesiones y modal de la TUI
│   ├── test_parallel.py   dos procesos reclamando el mismo ticket
│   ├── test_servers.py    inventario y limpieza de servidores MCP
│   ├── test_herdr.py      integración con herdr
│   ├── test_githook.py    commit-msg, pre-push y auditoría
│   └── test_hygiene.py    pruebas del propio auditor
├── selftest.py            shim compatible -> `tests/run.py`
├── bin/taiki              wrapper del CLI
├── bin/taiki-mcp          wrapper del servidor MCP
├── mcp_layer/
│   ├── schemas.py         tools, recursos, prompts e instrucciones
│   └── handlers.py        despachador declarativo y resolución de sesiones multi-entorno
└── ticketlib/
    ├── domain.py          estados, tipos, transiciones y errores
    ├── util.py            validación, normalización, resolución Git y FTS
    ├── db.py              conexión, transacciones reentrantes (BEGIN IMMEDIATE) y esquema
    ├── workspace.py       resolución de .taiki, configuración e init
    ├── worktree.py        worktrees por tarea y bootstrap
    ├── commits.py         validación de rutas y commit seguro
    ├── status.py          snapshot de sesiones, claims y solapamientos
    ├── env.py             variables TAIKI_* con alias TICKETS_*
    ├── render.py          presentación en texto, tablas ANSI/OSC 8, subtareas y JSON
    ├── agents.py          nombres y colores de agente (presentación compartida)
    ├── sessions/          sesiones de agente
    │   ├── backends.py    descubrimiento en OpenCode y Antigravity
    │   ├── activity.py    sesión viva y último actor por ticket
    │   └── terminals.py   terminales, directorios y lanzamiento
    ├── deeplink.py        enlaces ticket:// y handler de escritorio
    ├── githook.py         post-commit y commit-msg (autollenazado y guardia)
    ├── guard/             decisión de ticket activo
    │   ├── commands.py    heurística de comandos y rutas
    │   ├── hook.py        adaptadores de herramientas y payloads
    │   ├── context.py     cadena de sesión, ticket activo y épica
    │   └── policy.py      evaluate, auto-creación y mensajes
    ├── servers.py         inventario de procesos MCP, estados y limpieza segura
    ├── herdr.py           abrir ticket en herdr: focus, workspace y pestaña
    ├── audit.py           commits sin ticket, tickets estancados y sin resolución
    ├── repo.py            fachada pública del repositorio
    └── store/
        ├── common.py      filas, códigos y eventos
        ├── queries.py     consultas de tickets, filtrado por tags y subtareas
        ├── mutations.py   creación, edición y borrado de etiquetas
        ├── analytics.py   métricas agregadas y estadísticas
        ├── workflow.py    transiciones, claim atómico, cierre y confirmación en lote
        ├── relations.py   comentarios y enlaces
        └── tickets.py     fachada retrocompatible de store
```

## Instalación

No tiene dependencias externas: solo Python 3.11+ y su biblioteca estándar. Los
wrappers resuelven su propia ruta con `readlink -f`, así que funcionan tanto
desde el repo como a través de un enlace simbólico.

```bash
ln -sfn ~/Projects/taiki/bin/taiki ~/.local/bin/taiki
ln -sfn ~/Projects/taiki/bin/taiki-mcp ~/.local/bin/taiki-mcp
```

Los wrappers `bin/ticket` y `bin/tickets-mcp` se retiraron cuando el comando
`taiki` quedó asentado. Si todavía tienes hooks antiguos en otro repositorio,
migralos antes de actualizar:

```bash
cd /ruta/al/repo && taiki hook-install
```

### Migración desde `ticket`

Taiki se llamaba `ticket` hasta esta versión. El comando y los wrappers
antiguos ya no existen; la compatibilidad de **datos** se mantiene:

| Antes | Ahora | Notas |
| --- | --- | --- |
| `ticket <cmd>` | `taiki <cmd>` | el alias se retiró; usa `taiki` |
| MCP `tickets` (tools `tickets_*`) | MCP `taiki` (tools `taiki_*`) | `tickets_*` y los recursos `tickets://` siguen aceptándose |
| `TICKETS_DB`, `TICKETS_GUARD`, `TICKETS_GUARD_AUTO`, `TICKETS_HOOK`, `TICKETS_AGENT` | `TAIKI_*` | las antiguas se leen como alias; las nuevas tienen prioridad |
| Hooks `# >>> tickets ... >>>` | `# >>> taiki ... >>>` | `taiki hook-install` reescribe los bloques sin duplicarlos |

`taiki mcp install` retira la entrada histórica `tickets` de `opencode.json` y
registra `taiki`. Después de actualizar, reinicia OpenCode para que recargue el
servidor MCP con el nombre nuevo.

### Activar Taiki en un proyecto

```bash
cd ~/Projects/mi-proyecto
taiki init --workspace          # crea .taiki/ con su DB y config.json
taiki mcp install               # registra el MCP solo en este proyecto
taiki mcp install --antigravity # opcional: Antigravity solo tiene alcance global
```

`taiki mcp install` escribe la configuración del cliente en la raíz del
proyecto (`opencode.json`) y activa `guard` en `.taiki/config.json`. En OpenCode
cada proyecto arranca su propia instancia MCP con su propia DB; no hay un
servidor global siempre activo.

Para quitarlo:

```bash
taiki mcp uninstall             # retira el MCP y desactiva el guard
```

Atajo equivalente:

```bash
taiki init --workspace --mcp
```

### Antigravity

El CLI de Antigravity no ofrece alcance por proyecto: `agy mcp add` escribe en
`~/.gemini/config/mcp_config.json` para todos los proyectos. Taiki no lo toca
por defecto; si lo quieres ahí, instálalo explícitamente:

```bash
taiki mcp install --antigravity
```

### Otros clientes MCP

```json
{
  "mcpServers": {
    "taiki": {
      "command": "python3",
      "args": ["/home/rowlm/Projects/taiki/server.py"]
    }
  }
}
```

El cliente debe arrancar el proceso con el directorio del proyecto como `cwd`
(es lo habitual). El servidor resuelve `.taiki/` desde ahí.

### Herramientas MCP

El servidor expone 19 herramientas. El nombre real no lleva prefijo (`list`,
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

## CLI

```bash
taiki list                     # abiertos, del código más reciente al más antiguo
taiki list --all               # incluye completados, confirmados y cancelados
taiki list -s pendiente -p alta
taiki list --sort priority     # prioridad y antigüedad (orden antiguo)
taiki list --limit 20 --offset 20  # paginación
taiki list --tag noctalia      # filtrar por etiqueta
taiki list --tree              # épicas con sus tareas, indentadas
taiki list --flat              # todas las tareas, sin plegar
taiki list --parent T-0014     # solo las tareas de una épica
taiki list --json-summary --compact --limit 20  # JSON barato para agentes
taiki show T-0001 --json --compact              # detalle JSON en una línea
taiki show T-0001              # detalle completo (incluye jerarquía de subtareas)
taiki new -t "Revisar backups" -k mantenimiento -p alta --tags backup infra
taiki new -t "Migrar API" --paths src/api/ tests/api/  # rutas que posee el ticket
taiki new -t "Subtarea" --parent T-0014  # crea T-0014.01, T-0014.02…
taiki attach T-0014 T-0008 T-0009   # agrupa tickets existentes bajo una épica
taiki detach T-0008                  # la devuelve a la raíz
taiki renumber                      # simula pasar T-0023… a T-0022.01, .02…
taiki renumber --apply              # renombra los códigos planos
taiki repair-gaps                   # simula cubrir T-XXXX que nunca existieron
taiki repair-gaps --apply           # los cubre con tickets genéricos cancelados
taiki claim T-0001
taiki claim T-0001 --worktree  # reclama y crea .worktrees/T-0001
taiki claim T-0001 --steal     # recupera un claim caducado de otra sesión
taiki heartbeat T-0001         # renueva el lease en comandos largos
taiki status                   # sesiones, claims, worktrees y solapamientos
taiki status --sessions        # solo las sesiones activas
taiki worktree create T-0001  # .worktrees/T-0001 + rama agent/T-0001-…
taiki worktree list
taiki worktree merge T-0001 --delete  # solo si T-0001 está confirmado
taiki worktree remove T-0001 --delete-branch
taiki commit -m "..."          # commitea solo las rutas del ticket activo
taiki complete T-0001 -r "Qué se hizo y cómo se verificó" --commit abc1234
taiki confirm T-0001           # solo usuario
taiki confirm T-0003 T-0004    # varios a la vez, atómico
taiki reopen T-0001 --why "Reaparece el fallo"
taiki reject T-0001 --why "Falta cubrir el caso vacío"  # reabre y cuenta iteración
taiki comment T-0001 "Nota"
taiki transition T-0001 bloqueado --note "Espera respuesta"
taiki stats
taiki next
taiki scratch                   # ticket temporal oculto (código S-0001)
taiki scratch --list            # scratch vivos (código, caducidad, título)
taiki scratch --clean           # archiva los caducados
taiki scratch --promote S-0001  # lo devuelve a la tabla con un T-XXXX
taiki list --include-scratch    # los muestra en el listado
taiki export -o ~/tickets.json
taiki link T-0001 commit abc1234
taiki auto-link --commit HEAD  # enlaza los T-XXXX del mensaje
taiki guard --tool write --path ~/.config/x --session ses_abc  # ¿hay ticket?
taiki servers                  # inventario de servidores MCP
taiki servers --prune --apply  # limpia huérfanos y registros muertos
taiki servers --prune --apply --duplicates  # además, duplicados (conserva el nuevo)
taiki audit --days 14          # commits sin ticket, huecos y estado del MCP
taiki hook-install             # post-commit + commit-msg + pre-push
taiki                          # resumen rápido + abiertos
taiki open T-0004              # abre la sesión del agente en kitty
taiki scheme-install           # registra ticket:// para Ctrl+clic
taiki tui                      # TUI con toggles para confirmar
```

`--json` está disponible en `list`, `show`, `stats`, `next`, `guard` y `audit`.
En `list` y `show`, `--compact` y `--json-summary` implican `--json`: no hay
combinaciones que se ignoren en silencio.

### Épicas y tareas

Un objetivo con varias tareas se agrupa en una **épica**: un ticket normal con
tareas colgando de él. Así la lista no crece con cada paso.

- **Un solo nivel**: una épica tiene tareas; una tarea no puede tener hijas.
- **Nomenclatura**: las tareas heredan el prefijo del padre y un consecutivo,
  `T-0014.01`, `T-0014.02`… (en el árbol solo se muestra `.01`, `.02`). Los tickets que ya existían conservan su código.
- **Progreso**: `2/3` cuenta las tareas completadas, confirmadas o canceladas;
  `confirmed` distingue las confirmadas.
- **Lista**: por defecto solo raíces, con `(2/3)` y el aviso
  `N subtarea(s) oculta(s)`. `--flat` muestra todo; `--tree`, el árbol.
- **Cierre**: una épica no se completa con tareas abiertas ni se confirma
  hasta que sus tareas estén confirmadas. Reclamar una épica está bloqueado:
  se reclaman sus tareas.
- **Siguiente**: `next` nunca devuelve una épica, solo trabajo accionable.
- **Códigos planos**: si un proceso antiguo creó tareas con código normal
  (`T-0023` bajo `T-0022`), `taiki renumber --apply` los pasa a `.01`, `.02`…
  `taiki audit` los lista. Los adoptados con `attach` nunca se tocan.

```
T-0014       Jerarquía de tickets          [2/3]
    .01       Modelo y CLI                  confirmado
    .02       TUI con árbol                 completado
    .03       MCP, docs y migración         en_progreso
```

### Numeración sin huecos

Un `T-XXXX` solo se consume cuando nace un ticket que se verá en la tabla.
La regla evita el clásico hueco fantasma: un temporal que existió, se archivó
y deja un número sin ticket visible.

- **Temporales en su espacio**: los *scratch* usan `S-0001`, `S-0002`… con su
  propio contador. No tocan `next_ticket_number` ni la secuencia visible.
- **Promover**: `taiki scratch --promote S-0001` desarchiva el ticket, le
  asigna el siguiente `T-XXXX` y lo deja visible. Un scratch antiguo que ya
  tenía `T-XXXX` conserva su número si sigue libre.
- **Rastro al archivar**: `clean` (caducidad) y `drop` (descarte manual)
  escriben un evento `archivar` con el motivo; un ticket ya no desaparece en
  silencio.
- **Huecos históricos**: `taiki repair-gaps --apply` crea un ticket genérico
  cancelado con etiqueta `hueco` para cada número que nunca existió. El número
  queda cubierto y no se reutiliza, así una referencia antigua no apunta a
  trabajo nuevo.
- **Auditoría**: `taiki audit` separa *huecos reales* (números sin fila) de
  *códigos ocultos* (tickets archivados o temporales antiguos), con el motivo
  tomado del historial. `taiki show` marca explícitamente los archivados.

```bash
taiki new -t "Dashboard TUI 2.0" -p alta        # épica (T-0014)
taiki new -t "Panel de detalle" --parent T-0014 # T-0014.01
taiki attach T-0014 T-0008 T-0009                # adoptar tickets sueltos
taiki list --tree
```

### Listado largo y paginación

- Orden por defecto: **código descendente** (lo último creado primero).
  `--sort priority` recupera el orden por prioridad y antigüedad.
- La tabla muestra la columna **AGENTE** con el ticket asignado o, si aún no
  se ha reclamado, el agente del último evento (`-` solo si no hay actividad).
- `--limit` (50 por defecto) y `--offset` paginan. Cuando quedan más tickets,
  el CLI imprime `Mostrando X-Y de N` con el siguiente `--offset`.
- En JSON se incluyen `total`, `offset`, `limit` y `order`, y la herramienta
  MCP `list` acepta `offset`/`order` y devuelve `total`.
- Con SQLite + FTS esto escala sin problema a cientos o miles de tickets; lo
  que importa es paginar en la vista, no el tamaño de la tabla.

### Sesiones de agente y columna AGENTE

La sesión de un ticket se resuelve con el `session_id` del último evento que
siga existiendo en `~/.local/share/opencode/opencode.db` (`session_v2`); si esa
sesión fue borrada se prueba con la anterior y, como respaldo, con los enlaces
`kind='sesion'`.

**Es automático:** el servidor MCP resuelve la sesión en cada llamada
(`sessionId`, `session_id`, `AGY_SESSION_ID`, `OPENCODE_SESSION_ID`…) y la
guarda en `events.session_id`. Si el agente pasa `agent` al crear el ticket,
también se guarda como `assigned_to`, así que el ticket nace con dueño, color y
enlace; si no lo pasa, la columna usa el actor del último evento.

- **Sesión viva** → el agente se muestra con su color y es un enlace OSC 8
  `ticket://open/T-0004` (Ctrl+clic en kitty/alacritty).
- **Sesión borrada o inexistente** → el agente va en gris y sin enlace.
- Sin TTY, con `NO_COLOR` o con `TICKET_NO_LINKS=1` la tabla sale en texto
  plano (sin secuencias de escape).
- Los agentes internos de OpenCode (`build`, `plan`, `general`, `explore`) se
  guardan como **`opencode`**: son modos del mismo agente, no dueños distintos.
- En la lista, los tickets **confirmados** se atenúan y los **cancelados**
  además se tachan si el terminal lo soporta (si no, solo se atenúan), para
  que los pendientes resalten.

```bash
taiki scheme-install     # registra el handler ticket:// en ~/.local/share/applications
taiki open T-0004        # herdr si está disponible; si no, kitty
taiki open T-0004 --herdr --print-only   # plan de herdr sin tocar nada
taiki open T-0004 --new --space current  # otra sesión en el workspace actual
```

`taiki open` usa `TICKET_TERMINAL` (por defecto `kitty`) y abre
la terminal ejecutando `opencode --session <id>` o `agy --conversation <id>`
según corresponda dentro del directorio de la sesión. Si corre bajo UWSM,
confinará la app con `uwsm app --`.

**Con herdr disponible** (dentro de un pane con `HERDR_ENV=1`, desde la TUI
lanzada fuera —se detecta por el socket— o con `--herdr`) no se lanza una
terminal aparte: `taiki open` y la tecla `o` de la TUI usan la API de herdr.

1. Si la sesión del ticket ya vive en un pane (`agent_session.value`), solo
   hacen foco: no se duplica nada.
2. Si no, eligen el workspace cuyo `cwd` contenga el directorio del ticket (el
   ancestro más profundo). Si ninguno coincide, crean uno con la etiqueta del
   proyecto.
3. Abren una pestaña con solo el código (`T-XXXX`; el título la haría
   ilegible), se reutiliza si existe y tiene un pane libre, arrancan el agente
   con la sesión (`--session` / `--conversation`) o con un prompt inicial, y
   enlazan la sesión nueva al ticket.

Flags: `--herdr`, `--no-herdr`, `--new` (ignora la sesión viva),
`--space auto|current` y `--no-focus`. `--print-only` imprime el plan de
comandos sin ejecutar nada. Si no estás dentro de herdr, el comportamiento es
el de siempre (terminal externa).

**Directorio validado.** Antes de lanzar nada se comprueba que el directorio de
la sesión siga existiendo; si era un worktree borrado o la sesión se movió, se
usa el `path` del ticket y se avisa (`Aviso:` en el CLI, toast en la TUI). Si
tampoco existe, se aborta con un error explícito y no se abre ninguna terminal.
El panel de detalle muestra el **Directorio** de la sesión y lo marca cuando ya
no existe.

**Terminales soportadas** con su flag de directorio: `kitty` (`--directory`),
`alacritty`, `foot`, `ghostty` y `xfce4-terminal` (`--working-directory`),
`konsole` (`--workdir`), `gnome-terminal` (`--working-directory` + `--`) y
`wezterm` (`start --cwd` + `--`). Cualquier otra usa `--working-directory -e`
como respaldo.

**Nunca falla en silencio.** Se valida que el binario del agente (`opencode`,
`agy`/`antigravity`) esté en `PATH` y que la sesión siga viva (sin abrir una
nueva). Si la terminal muere al arrancar, el error incluye su stderr en vez de
perderse en `DEVNULL`.

### Confirmar en lote

```bash
taiki confirm T-0003 T-0004
```

`confirm` valida **todos** los códigos antes de escribir y aplica el lote en
una sola transacción:

- `completado` o `verificado` → se confirma.
- **épica `completado`/`verificado`** → se confirman primero sus tareas
  completadas o verificadas y después la épica, todo en la misma transacción.
  Si queda una tarea abierta no se confirma nada.
- `confirmado` → se omite con aviso (idempotente).
- otro estado o código inexistente → **no se confirma ninguno** y sale con
  código 1 listando los problemas por stderr.

### TUI (`taiki tui`)

```
 Tickets  abiertos 6 · en progreso 5 · por confirmar 3 · urgentes 0 · bloqueados 1
 11/13 visibles · ámbito: atención (2 oculto(s)) · sin filtros · orden: code
[ ] CÓDIGO  ESTADO      PRIOR.    AGENTE        TÍTULO               │ DETALLE
──────────────────────────────────────────────────────────────────────│
[ ]  T-0005 completado  alta      opencode      Abrir la sesión…     │ T-0005  Abrir la sesión…
[ ]  T-0004 completado  media     opencode      Listar tickets…      │ Estado      completado
```

La TUI es un dashboard de solo lectura salvo las acciones explícitas:
confirmar, reclamar, asignar, cambiar de estado, comentar, copiar el código,
enlazar un commit y abrir la sesión del agente.

- **Panel de detalle** (split a la derecha): estado, prioridad, tipo,
  proyecto, agente, sesión y directorio, fechas, etiquetas, descripción,
  resolución, subtareas, últimos comentarios, historial y enlaces. `tab` cambia
  el foco, `j/k`/`PgUp/PgDn` desplazan el panel, `d` lo oculta y `v` cicla
  entre **detalle**, feed de **actividad** y **mapa de actividad**. Por debajo
  de ~109 columnas se oculta solo.
- **Mapa de actividad** (estilo GitHub): rejilla de semanas × días con cuatro
  niveles de intensidad por cuartiles de los días con movimiento; caracteres
  `░▒▓█` para que se lea sin color y verdes 256 cuando hay. `t` alterna la
  métrica (`eventos`, `creados`, `completados`), `j/k` mueve el día, `h/l`
  desplaza semanas, `g/G` va al inicio o a hoy. El pie muestra el día
  seleccionado con su desglose, la leyenda y el rango visible; en paneles
  estrechos reduce las semanas. Respeta los filtros de proyecto y agente.
- **Cabecera de métricas**: abiertos, en progreso, pendientes de confirmar,
  urgentes, bloqueados y sin asignar. `urgentes` cuenta solo lo que requiere
  atención (ni confirmados ni cancelados), aunque cambies de ámbito. La segunda
  línea resume visibles, ámbito activo, cuántos tickets oculta, filtros, orden y
  si quedan páginas.
- **Filtros combinables** (`f`): ámbito (`atención` por defecto → `todos` →
  `completados` → `abiertos` → `cerrados`, con `s` para ciclar), estado,
  prioridad, tipo, agente, proyecto y etiqueta; `x` limpia. `completados`
  incluye `completado` y `verificado`; `cerrados` muestra solo `confirmado` y
  `cancelado`; `M` alterna esa vista con el ámbito anterior
  (la `m` minúscula abre el menú de acciones). Al abrir la TUI quedan ocultos
  los `confirmado` y `cancelado`, pero siguen cargados: cambiar de ámbito los
  recupera, y la selección solo confirma lo que pasa los filtros activos. En
  `atención`, un hijo `confirmado`/`cancelado` cuyo padre sigue abierto se
  muestra dentro del árbol, para no perder el contexto del trabajo activo. La
  búsqueda `/` usa FTS (título, cuerpo y código), añade las coincidencias en
  **comentarios** y resalta lo encontrado en la lista y en el panel; también
  mira proyecto, agente y etiquetas. `O` alterna el orden código/prioridad
  (`taiki tui --order priority`).
- **Paginación**: al llegar al final (o con `n`) se cargan 200 tickets más;
  el indicador `hay más` desaparece cuando ya está todo.
- `espacio`/`enter` marca (solo `completado` o `verificado`), `a` marca todos
  los confirmables, `A` limpia, `c` confirma con un modal de resumen.
  Al marcar una épica se marcan sus tareas completadas o verificadas (aunque
  estén plegadas); al confirmarla se confirman en orden tareas → épica.
- **Acciones** (`m`): reclamar, asignar, cambiar de estado (con motivo al
  reabrir), comentar, copiar código al portapapeles, enlazar commit y abrir
  sesión. Las acciones destructivas piden confirmación y todo pasa por el
  dominio (mismas validaciones y eventos que el CLI).
- **Auto-refresh**: cada 2 s (`--refresh N`, `0` lo desactiva) se consulta una
  huella barata (`MAX(updated_at)`, `MAX(events.id)`) y solo se recarga si
  cambió; cada 20 s se revalidan las sesiones. `R` pausa/activa y la barra
  muestra `auto 2s` o `auto pausa`. Las filas nuevas o cambiadas llevan `*`
  durante unos segundos y aparece un aviso.
- **Avisos**: cola de toasts con caducidad (éxito, info, error) en vez de un
  mensaje único; si SQLite está ocupada, la TUI avisa y sigue funcionando.
- `j/k`, flechas, `g/G`, `PgUp/PgDn` y la rueda del ratón mueven el cursor.
- Si el terminal es más pequeño que la tabla, la TUI lo avisa en vez de
  dibujar mal (mínimo 77x8; el panel necesita ~109 columnas).

### Automatización y backup

- `taiki auto-link` lee el mensaje del commit (`git show -s --format=%B`) y
  enlaza el hash a cada ticket existente mencionado como `T-XXXX`. Los códigos
  sin ticket se reportan por stderr sin romper el commit.
- `taiki hook-install` añade bloques gestionados al `post-commit`
  (autollenazado), al `commit-msg` (exige `T-XXXX` **confirmado** en el asunto)
  y al `pre-push` (bloquea commits nuevos con tickets sin confirmar); ejecutarlo
  dos veces no duplica nada y respeta hooks previos.
- `taiki audit` revisa commits sin ticket, tickets `en_progreso` sin actividad,
  cerrados sin resolución, tareas con código plano, huecos de numeración,
  cerrados **sin commit enlazado** y si el MCP en ejecución cargó el código
  actual.
- **Commit relacionado**: no hay flag de «commiteado»; se deriva de los enlaces
  `kind=commit`. `taiki show` y el panel de detalle muestran
  `Commit: abc1234` (o `+N` si hay varios). Al enlazar se normaliza al hash
  completo y se evita duplicar corto/largo; no se toca la tabla.
- **MCP desactualizado**: el servidor MCP es un proceso de larga vida; tras
  cambiar su código sigue con la versión anterior hasta reiniciar OpenCode.
  Al conectar guarda en `meta` una huella (`mcp_fingerprint`), el pid y la hora;
  `taiki audit` la compara con el código en disco y la TUI muestra
  `⚠ MCP desactualizado` en la cabecera. Reinicia OpenCode para quitarlo.
- **Códigos**: no se reciclan. Un hueco (`T-0018`) significa que ese número se
  asignó y luego se renombró o canceló; `taiki audit` lo explica con el evento
  `renumerar` o lo marca como «sin rastro».
- `scripts/tickets-backup.sh` usa `sqlite3 .backup` (seguro con la DB abierta)
  y conserva 14 días en `~/.local/share/tickets/backups/`. El timer de usuario
  `taiki-backup.timer` lo ejecuta a diario.

### Revisión, iteraciones y confirmación

El trabajo se entrega **antes** de publicarse:

1. El agente implementa y completa el ticket.
2. El usuario revisa y confirma con `taiki confirm` (o `c` en la TUI).
3. Solo entonces se hace commit y push.

El `commit-msg` exige que los tickets del asunto estén en **`confirmado`**, no
solo que existan; el `pre-push` revisa los commits nuevos y bloquea el push si
alguno referencia un ticket sin confirmar. Saltos: `[sin-ticket]` para commits
de mantenimiento o `TAIKI_HOOK=off`.

Si la revisión rechaza el trabajo:

```bash
taiki reject T-0033 --why "Falta el caso de error"
```

El ticket vuelve a `en_progreso`, deja un comentario con el motivo y, al
volver a completarlo, sube el contador **`iterations`**. Se muestra en
`taiki show`, en el panel de detalle de la TUI (`Iteraciones`) y en JSON:
`iterations: 2` significa dos entregas, la segunda tras un rechazo.

### Guard: ningún cambio sin ticket

Las instrucciones se las puede saltar un agente; el guard las hace cumplir.
`taiki guard` decide si una acción que escribe puede pasar:

- Si la sesión ya tiene un ticket `en_progreso` (o lo tiene un ancestro, en el
  caso de un subagente), pasa.
- Si la sesión creó un ticket `nuevo`/`pendiente`, lo reclama y **bloquea una
  vez** para que el agente lo revise, sin crear duplicados.
- Si no hay nada, crea un **ticket temporal** (`S-0001`, oculto) y lo reclama.
  **Por defecto el guard no crea nunca tickets visibles**: no aparece en
  `list`/`stats`/`next`/TUI ni consume números `T-XXXX`. Si el trabajo es real,
  `taiki scratch --promote S-0001` (recibe un `T-XXXX`); si no, caduca solo
  (TTL 120 min).

La política se elige con `guard_auto` en `.taiki/config.json` (o
`TAIKI_GUARD_AUTO`, que gana):

| valor | qué crea el guard |
|---|---|
| `scratch` (defecto) | siempre `S-####`, oculto; nunca toca la tabla |
| `auto` | si la sesión tiene una épica abierta, una tarea visible `T-XXXX.NN` suya; si no, scratch |
| `ticket` | siempre visible; raíz si no hay épica |
| `off` | bloquea sin crear nada |

Con `auto` o `ticket`, una épica solo se considera cerrada cuando se
**confirma**: mientras esté `en_progreso` o `completado` sigue admitiendo
trabajo derivado. Se exige que ya tenga tareas y sea del mismo proyecto (o sin
proyecto). Así, completar una épica y seguir trabajando en la misma sesión no
genera tickets sueltos.

El segundo intento ya pasa: el agente nunca se queda atascado, pero no puede
escribir sin que exista un ticket. Lecturas, rutas fuera del HOME (`/tmp`,
`/usr`, `/var/cache`…) y `TAIKI_GUARD=off` no se bloquean.

**Publicar no pide ticket.** El clasificador (`split_commands`) trocea el
comando por `;`, `&&`, `||`, `|` y saltos de línea respetando comillas, ignora
heredocs y redirecciones inocuas (`2>&1`, `>/dev/null`) y aplica una allowlist:
`git add/commit/push/tag/fetch/log/diff/…`, `taiki` y las
lecturas habituales
pasan; solo bloquea escrituras reales (redirección a un fichero, `sed -i`,
`rm/mv/cp/tee`, `git checkout/reset/clean/stash`, `find -delete`, `xargs rm`…).
Así `cd ~/.config && git push 2>&1 | tail` funciona sin ticket, y la puerta de
los commits sigue siendo `commit-msg` (T-XXXX confirmado) y `pre-push`.

El plugin global `~/.config/opencode/plugins/taiki-guard.js` engancha
`ctx.tool.hook("execute.before")` en OpenCode 2 (y `tool.execute.before` en V1)
y consulta al guard antes de `write`, `edit`, `patch`, `bash` y `execute`. Si el guard bloquea, lanza el mensaje como error para que el
modelo lo vea; cachea la autorización 30 s por sesión y falla abierto si el CLI
no responde.

```bash
taiki guard --tool write --path ~/.config/AGENTS.md --session ses_abc
taiki guard --tool bash --command "git commit -m 'x (T-0017)'" --session ses_abc
TAIKI_GUARD=off taiki guard --tool write --path ~/.config/x  # bypass
```

El `commit-msg` instalado por `taiki hook-install` exige un `T-XXXX` existente
**en el asunto**. Los códigos del cuerpo se avisan sin bloquear, para poder
explicar renumeraciones o historia. Para commits que no son trabajo de un
ticket, escribe `[sin-ticket]` o usa `TAIKI_HOOK=off`.

### Agentes en paralelo (worktrees)

Varias sesiones pueden trabajar a la vez sin pisarse:

- **Un worktree por tarea**: `taiki worktree create T-0001` crea
  `.worktrees/T-0001` con la rama `agent/T-0001-<slug>` (ignorada por Git).
  Copia `opencode.json` y `.opencode/`, nunca la base de datos.
- **DB compartida**: desde un worktree, `.taiki/taiki.db` se resuelve en el
  checkout principal con `git rev-parse --git-common-dir`; no hay una DB por
  worktree. Con `TAIKI_WORKTREE_ROOT` puedes cambiar la ubicación y con
  `taiki worktree adopt <ruta>` registrar uno creado por OpenCode.
- **Claim por sesión**: el ticket queda ligado a `owner_session` con un lease de
  30 min (`TAIKI_LEASE_MINUTES`). Otra sesión no puede reclamarlo; si el agente
  muere, `taiki claim T-XXXX --steal` lo recupera (solo si el lease caducó;
  `--force` lo fuerza) y `taiki audit` lo señala. `taiki heartbeat T-XXXX`
  renueva el lease durante comandos largos.
- **Un ticket activo por sesión**, con heartbeat en cada acción del guard.
- **Worktree obligatorio**: si otra sesión está activa, el guard bloquea las
  escrituras dentro del checkout cuando tu ticket no tiene worktree; la lectura
  sigue libre. Escape consciente: `TAIKI_SHARED_TREE=1`.
- **Commits acotados**: declara las rutas del ticket con `--paths` y usa
  `taiki commit -m "..."`. Rechaza el índice fuera de alcance o que solapa con
  otro ticket activo; el hook `pre-commit` (`taiki hook-check-index`) aplica la
  misma regla y exige `TAIKI_TICKET` si hay varios tickets activos.
- **Merge asistido**: `taiki worktree merge T-XXXX` hace rebase sobre la rama
  principal, ejecuta `tests/run.py` si existe (`--no-tests` para omitir),
  `merge --ff-only` y limpia con `--delete`. Nunca mergea si algo falla.
  Exige que el ticket esté `confirmado`: el commit vive en el worktree, pero
  `main` solo recibe lo que el usuario ya revisó. `--force` es el bypass
  consciente.
- **Estado**: `taiki status` (o la tecla `S` en la TUI) muestra sesiones,
  leases, ramas, worktrees y solapamientos. El plugin de recarga del MCP se
  aplaza si hay otra sesión con actividad reciente.
- **Reglas**: con otra sesión activa no uses `git add -A`, `git add .` ni
  `git stash`; fusiona de una rama a la vez.

```bash
taiki claim T-0001 --worktree
cd .worktrees/T-0001 && opencode
# ...trabajo en el worktree...
taiki commit -m "cambio acotado"
taiki complete T-0001 -r "Qué se hizo y cómo se verificó"
# el usuario revisa y confirma
taiki confirm T-0001
# solo ahora se fusiona la rama en main
taiki worktree merge T-0001 --delete
```

### Tickets temporales (scratch)

Para pruebas o exploración sin ensuciar la tabla:

```bash
taiki scratch -t "Probar migración" --ttl 30
# S-0001  scratch (en_progreso, caduca 2026-10-31T18:30:00-05:00)
taiki scratch --list
taiki scratch --clean
taiki scratch --promote S-0001   # vuelve a la tabla como T-XXXX
```

- Es un ticket normal con el marcador interno `_scratch` y `_scratch-sesion:<id>`
  (reservados: la etiqueta de tema `scratch` sigue siendo libre); el guard lo
  acepta como activo, pero **no aparece** en `list`, `stats`, `next` ni en la
  TUI. Se ve con `taiki list --include-scratch`.
- Su código es `S-####`, un contador aparte: crear temporales **no consume**
  números `T-XXXX` ni deja huecos en la tabla.
- `taiki scratch` **reutiliza** el scratch vivo de la misma sesión (no crea
  uno por comando) y archiva los caducados al crear otro. Las vistas (`list`,
  `next`, `stats`, `show`) también limpian los caducados al abrirse.
- `--ttl` son minutos (120 por defecto); al vencer se archiva con un evento
  `archivar` que deja rastro del motivo. `--promote` lo desarchiva y le da un
  `T-XXXX` (o conserva el suyo si es un scratch antiguo); `--drop` lo archiva
  también con evento. `confirm` los rechaza a propósito.
- `audit` no los mezcla con los huecos: si un scratch antiguo ocupa un
  `T-XXXX`, aparece como *código oculto*, no como hueco real.
- `export` los incluye: es un respaldo, no una vista.

### Servidores MCP

`taiki servers` cruza el registro `mcp_servers` con `/proc` para saber qué
procesos del servidor MCP están vivos y con quién:

```
    PID    PADRE  ESTADO          HUELLA        ARRANQUE             LATIDO
  88591    88565  desactualizado  0c2c274bd2c1  2026-10-31T15:03:03  2026-10-31T15:09:…
```

- **al día**: padre vivo y misma huella de código.
- **desactualizado**: vivo, pero arrancó con otro código; hay que recargarlo.
- **huérfano**: `ppid` 1 o padre muerto; ya no lo usa nadie.
- **muerto**: registro de un proceso que ya no existe.
- **sin registro**: vivo pero arrancó antes de existir el inventario.
- **duplicado**: varios procesos vivos con el mismo nombre y padre.

Al arrancar, el MCP borra del registro las filas de procesos que ya no
existen (autohigiene, sin matar nada). `taiki-servers.timer` ejecuta
`taiki servers --prune --apply` a diario.

`taiki servers --prune` simula la limpieza y `--apply` termina huérfanos y
borra registros muertos: espera la muerte tras `SIGTERM`, escala a `SIGKILL` y
solo borra la fila cuando consta muerto. Los duplicados no se tocan salvo con
`--duplicates`, que conserva el proceso más reciente.

El inventario no se limita a tickets: descubre cualquier MCP hijo del proceso
`opencode serve` (heurística por "mcp"/`server.py`), le deduce el nombre y lo
marca igual. Así un segundo servidor entra solo en `taiki servers`. **Nunca** se toca un proceso con padre vivo: una
sesión idle durante horas conserva su servidor, y `--prune` revalida el padre
justo antes de enviar el `SIGTERM`. El latido es informativo, no decide.

El plugin `taiki-servers.js` pide a OpenCode que recargue el MCP cuando la
huella no coincide, al quedar la sesión idle y una sola vez por huella (sin
bucles); después comprueba si el proceso reinició de verdad y lo deja en el
log. Mientras siga viejo, `taiki create` con `parent_id` **rechaza** la tarea
(compara la huella cargada con la del disco) para no numerarla mal a mitad de
turno. `taiki audit` y la cabecera de la TUI muestran huérfanos y
desactualizados con el comando para arreglarlo.

### Guard en otros agentes

OpenCode usa el plugin `taiki-guard.js`. Para cualquier agente con hooks de
pre-herramienta, el contrato es un JSON por stdin:

```bash
echo '{"tool_name":"write_file","tool_input":{"path":"/ruta"},"session_id":"ses_x","agent":"antigravity"}' \
  | taiki guard --hook
# {"decision":"deny","action":"created","ticket":"T-0031","reason":"…"}  (exit 2)
```

- `exit 0` permite y `exit 2` bloquea mostrando `reason` al modelo.
- Acepta alias (`write_file`, `edit_file`, `replace`, `run_shell_command`,
  `run_command`…) y rutas en `path`/`file_path`, comandos en `command`/`cmd`.
- El primer intento crea y reclama el ticket; el segundo pasa.
- Si el agente no tiene hooks, el `commit-msg` sigue siendo la red.

## Recursos y prompts MCP

El catálogo de 19 herramientas está en [Herramientas MCP](#herramientas-mcp) y
se genera desde `mcp_layer/schemas.py`. Aquí solo quedan los recursos y prompts:

Recursos: `taiki://inbox`, `taiki://stats`, `taiki://ticket/{code}`.
Prompts: `taiki:next`, `taiki:review`.

Resolución de sesiones automática compatible con `sessionId`, `session_id`, `AGY_SESSION_ID` y `ai.opencode/sessionID`.

## Flujo recomendado para agentes

1. Al empezar: `list` con `statuses=["pendiente"]` o `next`.
2. Antes de tocar código: `claim`. Si no hay ticket, créalo primero: el guard
   (cuando el proyecto lo tiene activado) bloquea la primera escritura y deja
   un ticket automático reclamado.
3. Trabajo nuevo descubierto: `create` **pasando `agent`**; el ticket queda
   asignado desde su creación.
4. Al terminar: `complete` con resolución y commit.
5. Nunca `confirmado`: eso lo hace el usuario con `taiki confirm`.

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

## Datos y respaldo

- La DB vive en `.taiki/taiki.db` del proyecto y está ignorada por Git.
- Respaldo lógico: `taiki export -o ~/tickets.json`.
- Respaldo físico: `sqlite3 .taiki/taiki.db ".backup /ruta/copia.db"`.
- `scripts/tickets-backup.sh` respalda las DB de los `.taiki/` encontrados y
  conserva 14 días.
- Para usar otra DB: `TAIKI_DB=/ruta/taiki.db taiki list` (o el alias
  `TICKETS_DB`). `--db` sigue funcionando.
- No guardes secretos en los tickets.

## Desarrollo

```bash
python3 tests/run.py                 # suite completa (el runner imprime el total)
python3 tests/run.py --list          # módulos disponibles
python3 tests/run.py --only guard    # una sola área, con su propia DB temporal
python3 tests/run.py -k tui -v       # filtrar por nombre y ver el desglose
python3 tests/run.py --audit         # ruido, duplicados, imports y techo de checks
python3 tests/run.py --strict-budget # falla si se supera el presupuesto de tiempo
python3 tests/run.py --bench         # benchmarks 20k (límites según CI/local)
python3 tests/run.py --verify        # todos los gates: suite, cobertura, ruff, mypy y benchmarks
python3 -m tests.benchmarks          # benchmarks por separado
ruff check .                         # lint (corrección + ARG)
mypy                                 # tipos de ticketlib (gradual)
coverage run tests/run.py && coverage json -o coverage.json
python3 tests/coverage_gate.py --run           # regenera cobertura y aplica el umbral por capa
python3 tests/coverage_gate.py coverage.json   # umbral por capa sobre un JSON existente
python3 selftest.py                  # compatibilidad (delega en tests/run.py)
TAIKI_DB=/tmp/opencode/taiki.db python3 cli.py list
```

`pyproject.toml` declara `requires-python >=3.11`, los entry points `taiki` y
`taiki-mcp`, la configuración de ruff/mypy y la cobertura. La CI de
`.github/workflows/ci.yml` ejecuta lint, tipos, la suite con
`--strict-budget`, la cobertura por capa y los benchmarks en cada push y pull
request. Los módulos de prueba se descubren automáticamente
(`tests/test_*.py`); no hay que editar el runner al añadir uno.

Cada módulo de la suite crea su propia base de datos temporal y restaura las
variables de entorno al terminar; `--failfast` detiene la ejecución en el primer
fallo y, sin él, un módulo roto no impide ejecutar los demás. El runner imprime
el total real de comprobaciones, que cubre filtros de tags, subtareas, concurrencia de transacciones reentrantes, claims por sesión y leases, worktrees y resolución de la DB compartida, commit acotado por rutas y solapamientos, estado de sesiones, resolución Git, confirmación en lote atómica, sesiones de agente (existencia, fallback, colores, directorio validado, flags por terminal y fallo de arranque con stderr), enlaces `ticket://`, estado de la TUI (ámbito de atención por defecto y ciclo de ámbitos con cerrados, atajo `M` de alternancia, hijos cerrados visibles si el padre sigue activo, contador de urgentes pendientes, filtros combinables, toasts, novedades, paginación, panel de detalle con directorio, mapa de actividad con calendario/cuartiles/métricas, snapshot y sondeo, búsqueda FTS con comentarios y resaltado, épicas, jerarquía y códigos T-XXXX.N), acciones de administración, guard (auto-creación, reutilización de pendientes, bypass y heurística de comandos), hooks y auditoría, renumeración de códigos planos, inventario y limpieza de servidores MCP, apertura en herdr, iteraciones y bloqueo de commit/push sin confirmar, y despacho MCP.

### Presupuesto y cuarentena

- `tests/budget.toml` fija un presupuesto de tiempo (suite y módulo) y un
  **techo** de comprobaciones con tolerancia. En local superarlo genera un
  **aviso accionable**; con `--strict-budget` (lo que usa la CI) el tiempo es un
  **gate duro** y la suite termina con código 1. Bajar comprobaciones no avisa:
  el contador es un techo, no un objetivo.
- Presupuestos **runner-aware**: con `CI=true` (o `TAIKI_CI=true`) se aplica el
  perfil `[ci.suite]`/`[ci.module]` de `budget.toml` (8 s de suite y 1,5 s por
  módulo). `TAIKI_SUITE_BUDGET` y `TAIKI_MODULE_BUDGET` sobrescriben cualquier
  valor. Los benchmarks usan 10 ms/500 ms en local y 25 ms/1.500 ms en CI, con
  `TAIKI_BENCH_CONNECT_MS` / `TAIKI_BENCH_EXPORT_MS` como override.
- Política de tests: un comportamiento, un test en la capa más baja; un bug,
  una regresión en la capa que falló; MCP/CLI solo contrato y TUI solo lógica
  pura. Borrar tests es válido si la cobertura no baja; añadir por encima del
  techo obliga a podar o justificarlo.
- La cobertura se mide **aparte** del presupuesto de tiempo: la CI ejecuta
  primero `tests/run.py --strict-budget` sin instrumentar y después
  `coverage run` + `tests/coverage_gate.py`. Así el coste de coverage no infla
  el límite de 4 s.
- `tests/coverage_gate.py` aplica umbrales por capa definidos en
  `[tool.coverage.taiki]` de `pyproject.toml` (ratchet: solo se suben). El gate
  compara **líneas cubiertas**, no ramas (`branch = true` es solo para el
  informe). Mide `ticketlib` y `mcp_layer` completos, `commands` tal como lo
  ejerce la suite (los subprocesos de `bin/taiki` no se instrumentan) y `tui`
  sin `app.py`, `view.py` ni `actions_ui.py` (curses/UI); no se persigue
  cobertura de curses.
- `tests/quarantine.toml` permite aparcar un módulo con `reason`, `owner` y
  `expires`. Mientras está vigente se ejecuta pero un fallo no bloquea; al
  caducar, la suite falla para forzar la revisión.
- `--audit` informa de mensajes duplicados, imports y seeds sin uso, módulos sin
  aserciones y desviación sobre el techo. Devuelve código 1 si hay fallos y
  respeta `--strict-budget`. No modifica ni elimina pruebas.
- No hay borrado automático por antigüedad: un test antiguo que protege una
  invariante sigue siendo valioso.

El servidor MCP no tiene dependencias externas y se puede probar por tubería:

```bash
printf '%s\n' \
  '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-11-25"}}' \
  '{"jsonrpc":"2.0","id":2,"method":"tools/list"}' \
  | python3 server.py
```

## Instancias MCP y aislamiento

- Cada proyecto arranca su propia instancia MCP, con su `cwd` como workspace. La
  instancia resuelve su `.taiki/` al vuelo y nunca comparte DB con otro proyecto.
- El registro de procesos (`~/.local/share/taiki/registry.db`) es global y solo
  guarda PID, padre, huella, versión y workspace. No contiene tickets.
- `taiki servers` muestra la columna `ESPACIO` para saber qué instancia sirve a
  qué proyecto; `--prune` limpia huérfanos sin tocar procesos con padre vivo.
- No hay MCP global ni base de datos global. Un directorio sin `.taiki/` no
  arranca una instancia útil: el servidor responde con el error de
  `taiki init --workspace`.
- Los códigos (`T-0001`) son por proyecto. `taiki open T-0001` resuelve con el
  `cwd`; enlazar entre proyectos necesitará incluir el workspace en el enlace
  (pendiente).

## Notas de diseño

- El estado es un contrato de flujo, no una frontera de seguridad: un agente con
  acceso al shell podría ejecutar `taiki confirm`. La separación evita errores
  accidentales, no un atacante.
- SQLite en modo WAL con `busy_timeout` y transacciones `BEGIN IMMEDIATE` reentrantes permite alta concurrencia segura sin condiciones de carrera.
- Resolución de ámbito inteligente: `resolve_scope` localiza la raíz del repositorio Git hacia arriba para asociar el proyecto correcto incluso al ejecutar desde subdirectorios.
- Los códigos (`T-0001`) son legibles y estables; los ids internos no se exponen en el flujo normal.
