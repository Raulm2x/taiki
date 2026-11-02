# Uso de Taiki

Este documento recoge el uso diario del CLI, la jerarquía de tickets, la TUI
y las rutas de revisión y confirmación. Forma parte de la documentación de
Taiki, junto con el resto de guías de `docs/`.

- [Volver al README](../README.md)
- [Índice de documentación](README.md)

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

`--json` está disponible en `list`, `show`, `stats`, `next`, `guard`, `audit`,
`memory`, `remember`, `brief` y `skills`.
En `list` y `show`, `--compact` y `--json-summary` implican `--json`: no hay
combinaciones que se ignoren en silencio.

### Memoria y brief

La memoria guarda hechos, convenciones y pitfalls reutilizables; no sustituye
al ticket ni guarda transcripciones. Las propuestas de los agentes quedan
pendientes hasta que el usuario las aprueba. El detalle está en
[Memoria](memoria.md).

```bash
taiki remember "Prefiero commits en español"        # preferencia (scope user)
taiki remember "El gate exige ruff" --ticket T-0045.05

taiki memory add --scope project --kind convention \
  --title "Los docs van en docs/" \
  --content "La documentación vive en docs/ y se enlaza desde docs/README.md." \
  --evidence T-0045.05 --pin

taiki memory list                      # vigentes y propuestas
taiki memory list --kind pitfall
taiki memory show M-0001               # detalle, uso y ledger
taiki memory pending                   # propuestas pendientes
taiki memory pending --diff            # diff frente a la memoria sustituida
taiki memory approve M-0002            # solo el usuario
taiki memory reject M-0003 --why "Fallo transitorio"
taiki memory edit M-0001 --content "Texto corregido"
taiki memory pin M-0001                # fijar en el brief
taiki memory unpin M-0001
taiki memory rm M-0001 --why "Ya no aplica"  # archiva, no borra

taiki memory candidates                # candidatos deterministas del histórico
taiki memory candidates --since 30d --kind pitfall --limit 5
taiki memory review                    # paquete para el curator
taiki memory review --write            # .taiki/generated/memory-review.json
taiki memory review --run-command      # comando opt-in → propuestas
taiki memory review --run-command --dry-run

taiki memory curate                    # caduca/archiva/deduplica sin borrar
taiki memory curate --dry-run
taiki memory curate --json
taiki memory stats                     # conteos, presupuesto, uso y routing
taiki memory stats --json
taiki memory impact --days 30          # tendencias, sin causalidad
taiki memory impact --days 30 --json

taiki memory export .taiki/generated/memoria.json
taiki memory export .taiki/generated/memoria.md --format markdown
taiki memory import .taiki/generated/memoria.json --strategy keep --dry-run
taiki memory import .taiki/generated/memoria.json --strategy replace
taiki memory rollback 42               # por asiento de ledger
taiki memory rollback 42 --dry-run
taiki memory rollback --snapshot .taiki/generated/memoria.json

taiki brief                            # brief operativo
taiki brief --write                    # .taiki/generated/brief.md
taiki brief --section project --budget 2000

taiki memory playbook add --slug revisar-pr \
  --title "Revisar PR" \
  --description "Checklist para revisar un pull request." \
  --content "1. Lee el diff. 2. Ejecuta los gates." \
  --support references/checklist.md \
  --evidence T-0047.05
taiki memory playbook list             # activos y propuestos
taiki memory playbook list --all       # incluye archivados
taiki memory playbook show revisar-pr
taiki memory playbook export --all     # .taiki/generated/skills/<slug>/
taiki memory playbook archive revisar-pr --why "Ya no aplica"

taiki skills suggest                   # skills curadas para el stack detectado
taiki skills suggest --limit 10 --json
taiki skills install fastapi --yes     # playbook propuesto, pendiente de aprobar
taiki skills list                      # instaladas, estado y origen
taiki skills remove fastapi --why "Ya no uso FastAPI"  # archiva
taiki skills sync                      # exporta activos y retira obsoletos
```

`memory candidates` agrupa rework, rechazos, bloqueos, cierres costosos,
routing, hotspots y directivas del usuario. Cada candidato lleva evidencia,
muestra y confianza; el motor no escribe memoria. `memory review` reúne
candidatos, memorias vigentes, tickets recientes, umbrales e instrucciones, y
`--write` lo guarda de forma atómica para que lo lea el curator.

El **curator** es un subagente que lee el paquete de revisión, propone como
máximo 5 memorias y prefiere `patch`/`supersede` sobre altas nuevas. No aprueba,
no edita código y no lanza subagentes. El conductor lo ejecuta en background
cada 5 tickets verificados/cerrados o cuando hay 3 o más candidatos, y publica
las propuestas pendientes. La aprobación sigue siendo del usuario.

`memory curate` aplica caducidad, archivo y dedupe deterministas: `active →
stale`, `stale → archived`, consolida duplicados y recorta el presupuesto por
menor prioridad. `pin`/`lock` quedan exentos y nada se borra ni se aprueba.
`--dry-run` muestra el plan sin escribir. `memory stats` resume estado, scope,
kind, presupuesto, propuestas, uso y routing; `memory impact` muestra
tendencias semanales de cierres, intentos, lead time y verificación. El impacto
es descriptivo: no demuestra causalidad.

`memory export` escribe un snapshot JSON o Markdown con versión y checksum;
`memory import` valida el checksum y acepta `--strategy keep|replace|new` y
`--dry-run`, informando conflictos. `memory rollback <ledger-id>` restaura el
estado anterior a un asiento; `--snapshot <fichero>` restaura un snapshot
exportado y archiva lo que sobra. Ambos son auditables y admiten `--dry-run`.
El auto-curado (`memory.auto_curate`) y `memory.review_command` son opt-in: el
segundo recibe el paquete por stdin y solo deja propuestas para el usuario.

`taiki mcp install` regenera el brief y lo añade a las instrucciones del
proyecto para que el agente lo reciba al empezar. Una memoria de origen agente
exige evidencia y no puede aprobarse desde un agente: `approve` y `reject`
requieren el actor `usuario`.

Los **playbooks** son procedimientos completos con evidencia y ficheros de
apoyo (`references/`, `scripts/`, `assets/` o `templates/`). `add` acepta
`--content` o `--file` para el cuerpo y `--support <ruta>` repetible; el apoyo
se lee del disco y se guarda con su nombre. `export` escribe
`.taiki/generated/skills/<slug>/SKILL.md` y sus apoyos; `--all` exporta los
activos y retira los skills de playbooks archivados. `archive` exige `--why` y
nunca borra. Los scripts se guardan como texto y no se ejecutan.

El brief muestra solo el índice (slug, título, descripción y código). El cuerpo
se carga bajo demanda con `memory_playbook(slug)` o el recurso
`taiki://playbook/{slug}`. `taiki mcp install` sincroniza
`.taiki/generated/skills` con la lista `skills` de OpenCode y `uninstall` la
retira.

### Skills curadas de AutoSkills

`taiki skills` integra el catálogo de [AutoSkills](https://www.autoskills.sh/)
como fuente opcional de playbooks. La detección de stack es local (manifiestos,
lockfiles y configuración) y no usa Node. `suggest` cruza ese stack con el
registro y muestra fuente, commit, hash, revisión y comprobación de seguridad.

`install` es exclusivo del usuario/CLI y descarga el bundle verificado con
SHA-256 y `bundleHash`; el resultado nace como playbook `proposed` (`M-####`) y
solo se activa con `taiki memory approve`. No existe tool MCP de instalación:
`skills_suggest` solo lee. `list` muestra las instaladas, `remove` archiva (no
borra) y `sync` exporta los playbooks activos y retira los obsoletos. El
registro se cachea 24 h en `.taiki/generated/skills/.cache/registry.json` y
`TAIKI_SKILLS_REGISTRY` permite usar un registro local o un espejo.

Los scripts de una skill se guardan como texto y nunca se ejecutan. La
desinstalación es `taiki skills remove <nombre>` seguida de `taiki skills sync`;
`taiki mcp uninstall` retira el MCP, el brief y la ruta de skills de
`opencode.json`. Detalle y límites: [Memoria](memoria.md#skills-curadas-de-autoskills).

### Routing advice y checklist

`agents(kind=...)` y `schedule` incluyen un bloque `advice`: el perfil que
mejor resolvió tareas de ese tipo y rutas en el histórico cerrado. Solo aparece
con al menos 5 muestras e incluye `sample`, `first_pass`, `confidence`
(cota inferior de Wilson), `avg_attempts`, `variant`, `steps` y `reason`. Es
una sugerencia: **no cambia la configuración ni lanza agentes**.

Antes de revisar, el reviewer pide
`memory_checklist(kind="pitfall"|"convention", paths=[...], limit=5)`. Ahí
`kind` es el tipo de memoria, no el tipo de ticket; cada entrada es compacta y
lleva evidencia. Completa con `memory_search` de pitfalls y, si detecta un
fallo recurrente, propone un `pitfall` sin aprobarlo.

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
- **Pantalla de memoria** (`Y`): propuestas, entradas activas, stale,
  playbooks y presupuesto sin salir del dashboard. La cabecera muestra los
  conteos por estado, el presupuesto de `project`/`user`, los playbooks y los
  umbrales de frescura/archivo. `h/l` (o `←/→`) ciclan los filtros
  `propuestas → activas → stale → playbooks → todas`; `j/k`, `PgUp/PgDn` y
  `g/G` recorren la lista, `tab` cambia el foco al detalle y `?` muestra la
  ayuda. La lista muestra código, estado, tipo y título; el detalle añade
  contenido, evidencia, uso, presupuesto y ficheros del playbook.
  Las acciones son explícitas del usuario: `a` aprueba una propuesta, `x` la
  rechaza con un motivo, `p` fija o desfija en el brief, `r` archiva con
  confirmación y `o` exporta el playbook a `.taiki/generated/skills/<slug>/`.
  Aprobar y rechazar nunca son automáticos y ninguna acción borra: archivar
  conserva el histórico en el ledger. Si la memoria está deshabilitada o
  vacía, la pantalla lo avisa y sigue siendo navegable. `q`, `Esc` o `Y`
  vuelven a la lista de tickets.
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

