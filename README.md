# Taiki

Taiki es un gestor local de tickets en SQLite para que los agentes de IA
registren y coordinen trabajo (mantenimiento, funcionalidades, bugs,
documentación, seguridad) y para que el usuario revise y confirme lo hecho.

- **Proyecto**: `~/Projects/taiki` (repo independiente; `~/.config/tickets` solo
  conserva un shim de compatibilidad).
- **Servidor MCP**: `tickets` (stdio). No hay servidor global: se instala por
  proyecto con `ticket mcp install`.
- **CLI**: `ticket` para uso humano.
- **Base de datos**: una por proyecto en `.taiki/taiki.db`. Sin base de datos
  global: usar Taiki es una decisión de cada proyecto.

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
3. Si no hay ninguno, error claro con `ticket init --workspace`.

`config.json`:

```json
{
  "version": 1,
  "project": "mi-proyecto",
  "name": "Mi proyecto",
  "guard": false,
  "defaults": {"kind": "otro", "priority": "media"},
  "ui": {"scope": "atencion", "refresh": 2.0}
}
```

- `guard` es opt-in: `ticket mcp install` lo activa; `ticket mcp uninstall` lo
  desactiva.
- `local.json` permite sobrescribir ajustes que no deben viajar en Git.

## Estados

```
nuevo → pendiente → en_progreso → completado → confirmado
                 ↘ bloqueado ↗
                 ↘ cancelado ↗ (reabrible)
```

- `confirmado` solo lo puede aplicar el usuario desde el CLI.
- Reabrir un ticket (`completado`, `confirmado` o `cancelado`) exige un motivo.
- Cada cambio queda registrado en el historial con actor, sesión y nota.

## Estructura

```
taiki/
├── server.py              entrada MCP (transporte stdio JSON-RPC)
├── cli.py                 fachada del CLI (compatibilidad y `bin/ticket`)
├── parser.py              argumentos y `main()`
├── commands/              un módulo por área de comandos
│   ├── common.py          conexión, salida y lectura de cuerpo
│   ├── tickets.py         list, show, new, update, claim, next, stats, export
│   ├── workflow.py        transition, confirm, reopen, complete, comment
│   ├── git.py             link, auto-link, hook-install, audit
│   ├── guard.py           ticket guard (¿hay ticket activo para esta acción?)
│   ├── mcp.py             instala/retira el MCP solo en el proyecto actual
│   ├── servers.py         ticket servers (inventario y limpieza de MCP)
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
│   ├── test_servers.py    inventario y limpieza de servidores MCP
│   ├── test_herdr.py      integración con herdr
│   ├── test_githook.py    commit-msg, pre-push y auditoría
│   └── test_hygiene.py    pruebas del propio auditor
├── selftest.py            shim compatible -> `tests/run.py`
├── bin/ticket             wrapper del CLI
├── bin/tickets-mcp        wrapper del servidor MCP
├── mcp_layer/
│   ├── schemas.py         tools, recursos, prompts e instrucciones
│   └── handlers.py        despachador declarativo y resolución de sesiones multi-entorno
└── ticketlib/
    ├── domain.py          estados, tipos, transiciones y errores
    ├── util.py            validación, normalización, resolución Git y FTS
    ├── db.py              conexión, transacciones reentrantes (BEGIN IMMEDIATE) y esquema
    ├── workspace.py       resolución de .taiki, configuración e init
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
ln -sfn ~/Projects/taiki/bin/ticket ~/.local/bin/ticket
ln -sfn ~/Projects/taiki/bin/tickets-mcp ~/.local/bin/tickets-mcp
```

### Activar Taiki en un proyecto

```bash
cd ~/Projects/mi-proyecto
ticket init --workspace          # crea .taiki/ con su DB y config.json
ticket mcp install               # registra el MCP solo en este proyecto
ticket mcp install --antigravity # opcional: Antigravity solo tiene alcance global
```

`ticket mcp install` escribe la configuración del cliente en la raíz del
proyecto (`opencode.json`) y activa `guard` en `.taiki/config.json`. En OpenCode
cada proyecto arranca su propia instancia MCP con su propia DB; no hay un
servidor global siempre activo.

Para quitarlo:

```bash
ticket mcp uninstall             # retira el MCP y desactiva el guard
```

Atajo equivalente:

```bash
ticket init --workspace --mcp
```

### Antigravity

El CLI de Antigravity no ofrece alcance por proyecto: `agy mcp add` escribe en
`~/.gemini/config/mcp_config.json` para todos los proyectos. Taiki no lo toca
por defecto; si lo quieres ahí, instálalo explícitamente:

```bash
ticket mcp install --antigravity
```

### Otros clientes MCP

```json
{
  "mcpServers": {
    "tickets": {
      "command": "python3",
      "args": ["/home/rowlm/Projects/taiki/server.py"]
    }
  }
}
```

El cliente debe arrancar el proceso con el directorio del proyecto como `cwd`
(es lo habitual). El servidor resuelve `.taiki/` desde ahí.

## CLI

```bash
ticket list                     # abiertos, del código más reciente al más antiguo
ticket list --all               # incluye completados, confirmados y cancelados
ticket list -s pendiente -p alta
ticket list --sort priority     # prioridad y antigüedad (orden antiguo)
ticket list --limit 20 --offset 20  # paginación
ticket list --tag noctalia      # filtrar por etiqueta
ticket list --tree              # épicas con sus tareas, indentadas
ticket list --flat              # todas las tareas, sin plegar
ticket list --parent T-0014     # solo las tareas de una épica
ticket show T-0001              # detalle completo (incluye jerarquía de subtareas)
ticket new -t "Revisar backups" -k mantenimiento -p alta --tags backup infra
ticket new -t "Subtarea" --parent T-0014  # crea T-0014.01, T-0014.02…
ticket attach T-0014 T-0008 T-0009   # agrupa tickets existentes bajo una épica
ticket detach T-0008                  # la devuelve a la raíz
ticket renumber                      # simula pasar T-0023… a T-0022.01, .02…
ticket renumber --apply              # renombra los códigos planos
ticket claim T-0001
ticket complete T-0001 -r "Qué se hizo y cómo se verificó" --commit abc1234
ticket confirm T-0001           # solo usuario
ticket confirm T-0003 T-0004    # varios a la vez, atómico
ticket reopen T-0001 --why "Reaparece el fallo"
ticket reject T-0001 --why "Falta cubrir el caso vacío"  # reabre y cuenta iteración
ticket comment T-0001 "Nota"
ticket transition T-0001 bloqueado --note "Espera respuesta"
ticket stats
ticket next
ticket scratch                   # ticket temporal oculto para pruebas
ticket scratch --list            # scratch vivos (código, caducidad, título)
ticket scratch --clean           # archiva los caducados
ticket scratch --promote T-0040  # lo devuelve a la tabla
ticket list --include-scratch    # los muestra en el listado
ticket export -o ~/tickets.json
ticket link T-0001 commit abc1234
ticket auto-link --commit HEAD  # enlaza los T-XXXX del mensaje
ticket guard --tool write --path ~/.config/x --session ses_abc  # ¿hay ticket?
ticket servers                  # inventario de servidores MCP
ticket servers --prune --apply  # limpia huérfanos y registros muertos
ticket servers --prune --apply --duplicates  # además, duplicados (conserva el nuevo)
ticket audit --days 14          # commits sin ticket, huecos y estado del MCP
ticket hook-install             # post-commit + commit-msg + pre-push
ticket                          # resumen rápido + abiertos
ticket open T-0004              # abre la sesión del agente en kitty
ticket scheme-install           # registra ticket:// para Ctrl+clic
ticket tui                      # TUI con toggles para confirmar
```

`--json` está disponible en `list`, `show`, `stats`, `next`, `guard` y `audit`.

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
  (`T-0023` bajo `T-0022`), `ticket renumber --apply` los pasa a `.01`, `.02`…
  `ticket audit` los lista. Los adoptados con `attach` nunca se tocan.

```
T-0014       Jerarquía de tickets          [2/3]
    .01       Modelo y CLI                  confirmado
    .02       TUI con árbol                 completado
    .03       MCP, docs y migración         en_progreso
```

```bash
ticket new -t "Dashboard TUI 2.0" -p alta        # épica (T-0014)
ticket new -t "Panel de detalle" --parent T-0014 # T-0014.01
ticket attach T-0014 T-0008 T-0009                # adoptar tickets sueltos
ticket list --tree
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
ticket scheme-install     # registra el handler ticket:// en ~/.local/share/applications
ticket open T-0004        # herdr si está disponible; si no, kitty
ticket open T-0004 --herdr --print-only   # plan de herdr sin tocar nada
ticket open T-0004 --new --space current  # otra sesión en el workspace actual
```

`ticket open` usa `TICKET_TERMINAL` (por defecto `kitty`) y abre
la terminal ejecutando `opencode --session <id>` o `agy --conversation <id>`
según corresponda dentro del directorio de la sesión. Si corre bajo UWSM,
confinará la app con `uwsm app --`.

**Con herdr disponible** (dentro de un pane con `HERDR_ENV=1`, desde la TUI
lanzada fuera —se detecta por el socket— o con `--herdr`) no se lanza una
terminal aparte: `ticket open` y la tecla `o` de la TUI usan la API de herdr.

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
ticket confirm T-0003 T-0004
```

`confirm` valida **todos** los códigos antes de escribir y aplica el lote en
una sola transacción:

- `completado` → se confirma.
- **épica `completado`** → se confirman primero sus tareas completadas y después
  la épica, todo en la misma transacción. Si queda una tarea abierta no se
  confirma nada.
- `confirmado` → se omite con aviso (idempotente).
- otro estado o código inexistente → **no se confirma ninguno** y sale con
  código 1 listando los problemas por stderr.

### TUI (`ticket tui`)

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
  prioridad, tipo, agente, proyecto y etiqueta; `x` limpia. `cerrados` muestra
  solo `confirmado` y `cancelado`; `M` alterna esa vista con el ámbito anterior
  (la `m` minúscula abre el menú de acciones). Al abrir la TUI quedan ocultos
  los `confirmado` y `cancelado`, pero siguen cargados: cambiar de ámbito los
  recupera, y la selección solo confirma lo que pasa los filtros activos. En
  `atención`, un hijo `confirmado`/`cancelado` cuyo padre sigue abierto se
  muestra dentro del árbol, para no perder el contexto del trabajo activo. La
  búsqueda `/` usa FTS (título, cuerpo y código), añade las coincidencias en
  **comentarios** y resalta lo encontrado en la lista y en el panel; también
  mira proyecto, agente y etiquetas. `O` alterna el orden código/prioridad
  (`ticket tui --order priority`).
- **Paginación**: al llegar al final (o con `n`) se cargan 200 tickets más;
  el indicador `hay más` desaparece cuando ya está todo.
- `espacio`/`enter` marca (solo `completado`), `a` marca todos los
  completados, `A` limpia, `c` confirma con un modal de resumen.
  Al marcar una épica se marcan sus tareas completadas (aunque estén
  plegadas); al confirmarla se confirman en orden tareas → épica.
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

- `ticket auto-link` lee el mensaje del commit (`git show -s --format=%B`) y
  enlaza el hash a cada ticket existente mencionado como `T-XXXX`. Los códigos
  sin ticket se reportan por stderr sin romper el commit.
- `ticket hook-install` añade bloques gestionados al `post-commit`
  (autollenazado), al `commit-msg` (exige `T-XXXX` **confirmado** en el asunto)
  y al `pre-push` (bloquea commits nuevos con tickets sin confirmar); ejecutarlo
  dos veces no duplica nada y respeta hooks previos.
- `ticket audit` revisa commits sin ticket, tickets `en_progreso` sin actividad,
  cerrados sin resolución, tareas con código plano, huecos de numeración,
  cerrados **sin commit enlazado** y si el MCP en ejecución cargó el código
  actual.
- **Commit relacionado**: no hay flag de «commiteado»; se deriva de los enlaces
  `kind=commit`. `ticket show` y el panel de detalle muestran
  `Commit: abc1234` (o `+N` si hay varios). Al enlazar se normaliza al hash
  completo y se evita duplicar corto/largo; no se toca la tabla.
- **MCP desactualizado**: el servidor MCP es un proceso de larga vida; tras
  cambiar su código sigue con la versión anterior hasta reiniciar OpenCode.
  Al conectar guarda en `meta` una huella (`mcp_fingerprint`), el pid y la hora;
  `ticket audit` la compara con el código en disco y la TUI muestra
  `⚠ MCP desactualizado` en la cabecera. Reinicia OpenCode para quitarlo.
- **Códigos**: no se reciclan. Un hueco (`T-0018`) significa que ese número se
  asignó y luego se renombró o canceló; `ticket audit` lo explica con el evento
  `renumerar` o lo marca como «sin rastro».
- `scripts/tickets-backup.sh` usa `sqlite3 .backup` (seguro con la DB abierta)
  y conserva 14 días en `~/.local/share/tickets/backups/`. El timer de usuario
  `tickets-backup.timer` lo ejecuta a diario.

### Revisión, iteraciones y confirmación

El trabajo se entrega **antes** de publicarse:

1. El agente implementa y completa el ticket.
2. El usuario revisa y confirma con `ticket confirm` (o `c` en la TUI).
3. Solo entonces se hace commit y push.

El `commit-msg` exige que los tickets del asunto estén en **`confirmado`**, no
solo que existan; el `pre-push` revisa los commits nuevos y bloquea el push si
alguno referencia un ticket sin confirmar. Saltos: `[sin-ticket]` para commits
de mantenimiento o `TICKETS_HOOK=off`.

Si la revisión rechaza el trabajo:

```bash
ticket reject T-0033 --why "Falta el caso de error"
```

El ticket vuelve a `en_progreso`, deja un comentario con el motivo y, al
volver a completarlo, sube el contador **`iterations`**. Se muestra en
`ticket show`, en el panel de detalle de la TUI (`Iteraciones`) y en JSON:
`iterations: 2` significa dos entregas, la segunda tras un rechazo.

### Guard: ningún cambio sin ticket

Las instrucciones se las puede saltar un agente; el guard las hace cumplir.
`ticket guard` decide si una acción que escribe puede pasar:

- Si la sesión ya tiene un ticket `en_progreso` (o lo tiene un ancestro, en el
  caso de un subagente), pasa.
- Si la sesión creó un ticket `nuevo`/`pendiente`, lo reclama y **bloquea una
  vez** para que el agente lo revise, sin crear duplicados.
- Si no hay nada, crea un ticket y lo reclama. **Si la sesión tiene una épica
  abierta, el ticket nace como tarea suya** (`T-XXXX.NN`) en vez de como raíz.
  Una épica solo se considera cerrada cuando se **confirma**: mientras esté
  `en_progreso` o `completado` sigue admitiendo trabajo derivado. Se exige que
  la épica ya tenga tareas y sea del mismo proyecto (o sin proyecto).

Así, completar una épica y seguir trabajando en la misma sesión no genera
tickets sueltos: el trabajo nuevo queda donde corresponde y la confirmación del
padre seguirá esperando a que la tarea nueva termine.

**Sin épica, el ticket automático es un scratch oculto** (TTL 120 min): el
guard desbloquea igual, pero no aparece en `list`/`stats`/`next`/TUI. Si el
trabajo es real, `ticket scratch --promote T-XXXX`; si no, caduca solo.
`TICKETS_GUARD_AUTO` cambia la política: `scratch` (siempre oculto), `ticket`
(siempre visible) u `off` (bloquear sin crear nada).

El segundo intento ya pasa: el agente nunca se queda atascado, pero no puede
escribir sin que exista un ticket. Lecturas, rutas fuera del HOME (`/tmp`,
`/usr`, `/var/cache`…) y `TICKETS_GUARD=off` no se bloquean.

**Publicar no pide ticket.** El clasificador (`split_commands`) trocea el
comando por `;`, `&&`, `||`, `|` y saltos de línea respetando comillas, ignora
heredocs y redirecciones inocuas (`2>&1`, `>/dev/null`) y aplica una allowlist:
`git add/commit/push/tag/fetch/log/diff/…`, `ticket` y las lecturas habituales
pasan; solo bloquea escrituras reales (redirección a un fichero, `sed -i`,
`rm/mv/cp/tee`, `git checkout/reset/clean/stash`, `find -delete`, `xargs rm`…).
Así `cd ~/.config && git push 2>&1 | tail` funciona sin ticket, y la puerta de
los commits sigue siendo `commit-msg` (T-XXXX confirmado) y `pre-push`.

El plugin global `~/.config/opencode/plugins/tickets-guard.js` engancha
`ctx.tool.hook("execute.before")` en OpenCode 2 (y `tool.execute.before` en V1)
y consulta al guard antes de `write`, `edit`, `patch`, `bash` y `execute`. Si el guard bloquea, lanza el mensaje como error para que el
modelo lo vea; cachea la autorización 30 s por sesión y falla abierto si el CLI
no responde.

```bash
ticket guard --tool write --path ~/.config/AGENTS.md --session ses_abc
ticket guard --tool bash --command "git commit -m 'x (T-0017)'" --session ses_abc
TICKETS_GUARD=off ticket guard --tool write --path ~/.config/x  # bypass
```

El `commit-msg` instalado por `ticket hook-install` exige un `T-XXXX` existente
**en el asunto**. Los códigos del cuerpo se avisan sin bloquear, para poder
explicar renumeraciones o historia. Para commits que no son trabajo de un
ticket, escribe `[sin-ticket]` o usa `TICKETS_HOOK=off`.

### Tickets temporales (scratch)

Para pruebas o exploración sin ensuciar la tabla:

```bash
ticket scratch -t "Probar migración" --ttl 30
# T-0040  scratch (en_progreso, caduca 2026-10-31T18:30:00-05:00)
ticket scratch --list
ticket scratch --clean
```

- Es un ticket normal con el marcador interno `_scratch` y `_scratch-sesion:<id>`
  (reservados: la etiqueta de tema `scratch` sigue siendo libre); el guard lo
  acepta como activo, pero **no aparece** en `list`, `stats`, `next`, `audit`
  ni en la TUI. Se ve con `ticket list --include-scratch`.
- `ticket scratch` **reutiliza** el scratch vivo de la misma sesión (no crea
  uno por comando) y archiva los caducados al crear otro.
- `--ttl` son minutos (120 por defecto); al vencer, `ticket scratch --clean` lo
  archiva. `--promote` lo devuelve a la tabla conservando historial; `--drop`
  lo archiva. `confirm` los rechaza a propósito.
- `export` los incluye: es un respaldo, no una vista.

### Servidores MCP

`ticket servers` cruza el registro `mcp_servers` con `/proc` para saber qué
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
existen (autohigiene, sin matar nada). `tickets-servers.timer` ejecuta
`ticket servers --prune --apply` a diario.

`ticket servers --prune` simula la limpieza y `--apply` termina huérfanos y
borra registros muertos: espera la muerte tras `SIGTERM`, escala a `SIGKILL` y
solo borra la fila cuando consta muerto. Los duplicados no se tocan salvo con
`--duplicates`, que conserva el proceso más reciente.

El inventario no se limita a tickets: descubre cualquier MCP hijo del proceso
`opencode serve` (heurística por "mcp"/`server.py`), le deduce el nombre y lo
marca igual. Así un segundo servidor entra solo en `ticket servers`. **Nunca** se toca un proceso con padre vivo: una
sesión idle durante horas conserva su servidor, y `--prune` revalida el padre
justo antes de enviar el `SIGTERM`. El latido es informativo, no decide.

El plugin `tickets-servers.js` pide a OpenCode que recargue el MCP cuando la
huella no coincide, al quedar la sesión idle y una sola vez por huella (sin
bucles); después comprueba si el proceso reinició de verdad y lo deja en el
log. Mientras siga viejo, `tickets create` con `parent_id` **rechaza** la tarea
(compara la huella cargada con la del disco) para no numerarla mal a mitad de
turno. `ticket audit` y la cabecera de la TUI muestran huérfanos y
desactualizados con el comando para arreglarlo.

### Guard en otros agentes

OpenCode usa el plugin `tickets-guard.js`. Para cualquier agente con hooks de
pre-herramienta, el contrato es un JSON por stdin:

```bash
echo '{"tool_name":"write_file","tool_input":{"path":"/ruta"},"session_id":"ses_x","agent":"antigravity"}' \
  | ticket guard --hook
# {"decision":"deny","action":"created","ticket":"T-0031","reason":"…"}  (exit 2)
```

- `exit 0` permite y `exit 2` bloquea mostrando `reason` al modelo.
- Acepta alias (`write_file`, `edit_file`, `replace`, `run_shell_command`,
  `run_command`…) y rutas en `path`/`file_path`, comandos en `command`/`cmd`.
- El primer intento crea y reclama el ticket; el segundo pasa.
- Si el agente no tiene hooks, el `commit-msg` sigue siendo la red.

## Herramientas MCP

| Tool | Uso |
| --- | --- |
| `create` | Registrar trabajo nuevo (admite etiquetas y `parent_id` para tareas) |
| `list` | Filtrar por estado, tipo, prioridad, proyecto, asignado, texto, `tag`, `only_roots` o `parent_id` |
| `get` | Detalle con comentarios, enlaces, historial y subtareas vinculadas |
| `claim` | Reclamar y pasar a `en_progreso` (atómico con `BEGIN IMMEDIATE`) |
| `update` | Cambiar título, cuerpo, tipo, prioridad, proyecto, etiquetas o `parent_id` |
| `transition` | Cambiar de estado (valida la máquina de estados) |
| `complete` | Cerrar con resolución, commit y archivos |
| `comment` | Añadir notas al historial |
| `link` | Enlazar commit, archivo, URL o sesión |
| `stats` | Resumen por estado, tipo, prioridad y proyecto |
| `next` | Siguiente ticket pendiente |

Recursos: `tickets://inbox`, `tickets://stats`, `tickets://ticket/{code}`.
Prompts: `tickets:next`, `tickets:review`.

Resolución de sesiones automática compatible con `sessionId`, `session_id`, `AGY_SESSION_ID` y `ai.opencode/sessionID`.

## Flujo recomendado para agentes

1. Al empezar: `tickets_list` con `status="pendiente"` o `tickets_next`.
2. Antes de tocar código: `tickets_claim`. Si no hay ticket, créalo primero: el
   guard (cuando el proyecto lo tiene activado) bloquea la primera escritura y
   deja un ticket automático reclamado.
3. Trabajo nuevo descubierto: `tickets_create` **pasando siempre `agent`**; el
   ticket queda asignado a ese agente desde su creación.
4. Al terminar: `tickets_complete` con resolución y commit.
5. Nunca `confirmado`: eso lo hace el usuario con `ticket confirm`.

## Datos y respaldo

- La DB vive en `.taiki/taiki.db` del proyecto y está ignorada por Git.
- Respaldo lógico: `ticket export -o ~/tickets.json`.
- Respaldo físico: `sqlite3 .taiki/taiki.db ".backup /ruta/copia.db"`.
- `scripts/tickets-backup.sh` respalda las DB de los `.taiki/` encontrados y
  conserva 14 días.
- Para usar otra DB: `TAIKI_DB=/ruta/taiki.db ticket list` (o el alias
  `TICKETS_DB`). `--db` sigue funcionando.
- No guardes secretos en los tickets.

## Desarrollo

```bash
python3 tests/run.py                 # suite completa (el runner imprime el total)
python3 tests/run.py --list          # módulos disponibles
python3 tests/run.py --only guard    # una sola área, con su propia DB temporal
python3 tests/run.py -k tui -v       # filtrar por nombre y ver el desglose
python3 tests/run.py --audit         # ruido: duplicados, imports, seeds y línea base
python3 selftest.py                  # compatibilidad (delega en tests/run.py)
TICKETS_DB=/tmp/opencode/tickets.db python3 cli.py list
```

Cada módulo de la suite crea su propia base de datos temporal y restaura las
variables de entorno al terminar; `--failfast` detiene la ejecución en el primer
fallo y, sin él, un módulo roto no impide ejecutar los demás. El runner imprime
el total real de comprobaciones, que cubre filtros de tags, subtareas, concurrencia de transacciones reentrantes, resolución Git, confirmación en lote atómica, sesiones de agente (existencia, fallback, colores, directorio validado, flags por terminal y fallo de arranque con stderr), enlaces `ticket://`, estado de la TUI (ámbito de atención por defecto y ciclo de ámbitos con cerrados, atajo `M` de alternancia, hijos cerrados visibles si el padre sigue activo, contador de urgentes pendientes, filtros combinables, toasts, novedades, paginación, panel de detalle con directorio, mapa de actividad con calendario/cuartiles/métricas, snapshot y sondeo, búsqueda FTS con comentarios y resaltado, épicas, jerarquía y códigos T-XXXX.N), acciones de administración, guard (auto-creación, reutilización de pendientes, bypass y heurística de comandos), hooks y auditoría, renumeración de códigos planos, inventario y limpieza de servidores MCP, apertura en herdr, iteraciones y bloqueo de commit/push sin confirmar, y despacho MCP.

### Presupuesto y cuarentena

- `tests/budget.toml` fija un presupuesto de tiempo (suite y módulo) y una
  línea base de comprobaciones con tolerancia. Superarlo genera un **aviso
  accionable**, nunca un borrado.
- `tests/quarantine.toml` permite aparcar un módulo con `reason`, `owner` y
  `expires`. Mientras está vigente se ejecuta pero un fallo no bloquea; al
  caducar, la suite falla para forzar la revisión.
- `--audit` informa de mensajes duplicados, imports y seeds sin uso, módulos sin
  aserciones y desviación sobre la línea base. No modifica ni elimina pruebas.
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
- `ticket servers` muestra la columna `ESPACIO` para saber qué instancia sirve a
  qué proyecto; `--prune` limpia huérfanos sin tocar procesos con padre vivo.
- No hay MCP global ni base de datos global. Un directorio sin `.taiki/` no
  arranca una instancia útil: el servidor responde con el error de
  `ticket init --workspace`.
- Los códigos (`T-0001`) son por proyecto. `ticket open T-0001` resuelve con el
  `cwd`; enlazar entre proyectos necesitará incluir el workspace en el enlace
  (pendiente).

## Notas de diseño

- El estado es un contrato de flujo, no una frontera de seguridad: un agente con
  acceso al shell podría ejecutar `ticket confirm`. La separación evita errores
  accidentales, no un atacante.
- SQLite en modo WAL con `busy_timeout` y transacciones `BEGIN IMMEDIATE` reentrantes permite alta concurrencia segura sin condiciones de carrera.
- Resolución de ámbito inteligente: `resolve_scope` localiza la raíz del repositorio Git hacia arriba para asociar el proyecto correcto incluso al ejecutar desde subdirectorios.
- Los códigos (`T-0001`) son legibles y estables; los ids internos no se exponen en el flujo normal.
