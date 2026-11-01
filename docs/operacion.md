# Operación

Cómo funciona Taiki en marcha: workspaces, estados, orquestación, guard,
worktrees, tickets temporales, servidores MCP, datos y aislamiento.

← [README](../README.md) · [Índice de documentación](README.md)

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
`.taiki/config.json` y se consultan con la herramienta MCP `agents`. Los
agentes escriben el estado con las herramientas MCP (`claim`, `complete`,
`transition`, `checkpoint`, `comment`) para conservar `actor`,
`actor_type=agente` y `owner_session`; el CLI queda para el usuario y
atribuye sus acciones como tales. Tras `claim` con worktree, el worker llama
a `session_move` con la ruta devuelta; el conductor comprueba que la sesión
hija está en el worktree y que el commit vive en `agent/T-XXXX-*`, no en
`main`. Los permisos de shell solo dejan a los agentes lecturas de `taiki` y
`taiki commit`; cualquier write de estado va por MCP. `uvx` queda limitado a
`ruff` y `mypy`: `python3 tests/run.py --verify` los ejecuta internamente,
pero edit/shell/uvx/tests no son un sandbox de seguridad.

Routing de modelos: `documentacion` → `low`; mantenimiento/funcionalidad →
`medium`; `bug`/`seguridad` → `high`. Todos los perfiles worker usan
`opencode-go/deepseek-v4.1-flash` para no disparar el coste en tokens;
`worker-medium` va sin variante porque deepseek no expone `medium`, así que se
usa el default del cliente. El conductor lanza el perfil que devuelve
`agents`, no el `worker` genérico. Si el modelo no expone la variante, se usa el
default; nunca se escala en bucle. El límite de writers
simultáneos es `orchestrator.max_parallel` (3 por defecto). Antes de
paralelizar, la herramienta MCP `schedule` separa las tareas en `parallel`
(paths disjuntos) y `serial` (solapes, esquema, store core, config o sin
paths); las migraciones y los merges nunca van en paralelo.

`taiki status` (y el modal `S` de la TUI) muestra la cola del orquestador
(listos, en curso, verificando, bloqueados y por confirmar), los worktrees y
métricas: lead time, intentos medios, fallos de verificación y paralelismo
máximo observado. `status.sessions[].directory` expone el directorio de la
sesión viva para compararlo con el worktree. La supervisión usa `taiki
status` y la herramienta MCP `health` (no existe `taiki health`). «Por confirmar» suma `completado` y
`verificado`;
«verificando» es el subconjunto que el conductor ya validó. El coste y los
tokens quedan como `no disponible` mientras no haya telemetría del proveedor.

El smoke opt-in `python3 tests/live_orchestrator.py` crea un repo temporal,
dos tareas con worktree y verifica rama/commit/health; no se ejecuta en CI y
`session_move` queda como paso manual de OpenCode.

Una tarea con dependencias no está lista hasta que el prerrequisito está
`verificado` (o `confirmado`/`cancelado`): `completado` aún puede fallar la
verificación y no desbloquea trabajo derivado.


## Guard: ningún cambio sin ticket

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


## Agentes en paralelo (worktrees)

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
- **Merge asistido**: el usuario confirma; solo después el conductor mergea en
  orden de dependencias. `taiki worktree merge T-XXXX` hace rebase sobre la rama
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


## Tickets temporales (scratch)

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


## Servidores MCP

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


## Guard en otros agentes

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


## Datos y respaldo

- La DB vive en `.taiki/taiki.db` del proyecto y está ignorada por Git.
- Respaldo lógico: `taiki export -o ~/tickets.json`.
- Respaldo físico: `sqlite3 .taiki/taiki.db ".backup /ruta/copia.db"`.
- `scripts/tickets-backup.sh` respalda las DB de los `.taiki/` encontrados y
  conserva 14 días.
- Para usar otra DB: `TAIKI_DB=/ruta/taiki.db taiki list` (o el alias
  `TICKETS_DB`). `--db` sigue funcionando.
- No guardes secretos en los tickets.


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
