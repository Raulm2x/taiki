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

### Verificación por tipo

El gate de `verificado` depende del cambio: en **código**, la suite y los gates
deterministas (`python3 tests/run.py --verify`); en **documentación o
dotfiles**, el contenido y el alcance del commit (`git show --stat` /
`git show --name-only`) y que el fichero live coincida con lo commiteado; en
**sistema**, `verify.command` del workspace cuando aplique. Un workspace sin
suite (`tests/run.py`) usa `verify.command` como gate; si el comando no existe
o no aplica (por ejemplo, un glob de unidades vacío), se deja constancia y no
cuenta como fallo.


## Orquestación

Taiki incluye un conductor primario y los subagentes `worker`, `worker-low`,
`worker-medium`, `worker-high` y `reviewer` en `.opencode/agents/`. El conductor planifica, delega, verifica con
`python3 tests/run.py --verify` y marca `verificado`; el usuario confirma y solo
después el conductor mergea los confirmados. Las capacidades (tipos, variantes,
paralelismo y coste) viven en
`.taiki/config.json` y se consultan con la herramienta MCP `agents`. Los
agentes escriben el estado con las herramientas MCP (`claim`, `complete`,
`transition`, `checkpoint`, `comment`) para conservar `actor`,
`actor_type=agente` y `owner_session`; el CLI queda para el usuario y
atribuye sus acciones como tales. Tras `claim` con worktree, el worker llama
a `session_move` con la ruta devuelta; el conductor comprueba que la sesión
hija está en el worktree y que el commit vive en `agent/T-XXXX-*`, no en
`main`. Cualquier write de estado va por MCP y `uvx` queda limitado a `ruff` y
`mypy`: `python3 tests/run.py --verify` los ejecuta internamente.

### Permisos de shell

Cada perfil declara sus reglas en `.opencode/agents/*.md`. La cadena de escalado
es **usuario → conductor → workers**: el usuario confirma, el conductor ejecuta
y los workers piden.

- **Conductor**: fallback `allow`, los mismos privilegios que el agente `build`
  de OpenCode. No pregunta por comandos comunes ni desconocidos no
  destructivos; ejecuta lo que los workers le escalan, los gates y el merge de
  worktrees ya confirmados. En el flujo live también ejecuta `taiki sys`,
  `taiki commit`, `taiki mcp install` y `taiki hook-install`, con `taiki brief`
  como lectura. `taiki authorize`, `taiki review`, `taiki confirm` y
  `taiki memory approve|reject` siguen siendo exclusivos del usuario y
  conservan su deny explícito al final de la lista. El guardrail es una lista
  de denies explícitos que se evalúa después de los allows.
- **Workers**: fallback `deny`. Lo que no está en su lista blanca no se ejecuta
  ni genera un prompt al usuario. Si necesitan un comando denegado, publican un
  `checkpoint` o `comment` con el comando exacto, el motivo y el resultado
  esperado; el conductor lo ejecuta o autoriza una alternativa. Nunca
  reintentan en bucle ni piden permiso al usuario.
- **Reviewer**: fallback `deny` y solo lectura; ejecuta comprobaciones y
  devuelve hallazgos.

Dentro de la lista blanca de los workers:

- **Permitido sin preguntar**: `pwd`, `ls`, `cat`, `head`, `tail`, `sed -n`,
  `sed`, `echo`, `printf`, `md5sum`, `sha256sum`, `grep`, `rg`, `find`, `wc`,
  `diff`, `file`, `stat`, `mkdir -p docs`, `git show`, `git rev-parse` y las
  lecturas con `git -C <worktree>` (`show`, `diff`, `log`, `rev-parse`);
  además de `python3 tests/*`, `timeout * python3 tests/*`,
  `python3 -m tests.*`, `git status/diff/log/add/commit`, `git rebase <rama>`
  (`--continue`, `--abort`, `--skip`), `git add .opencode/*`,
  `TAIKI_TICKET=* taiki commit *`, las lecturas de `taiki` y `taiki commit`.
  El rebase forma parte del ciclo del worker antes de integrar; nunca uses
  `git rebase -i` ni `--exec` (sin TTY se cuelgan): resuelve conflictos con
  `edit`, `git add` y `git rebase --continue`. `echo`/`printf`/`sed`/`md5sum` son para inspección
  puntual: la edición va por `edit`/`read`, no por shell. No uses `cd` (la
  sesión ya está en el worktree), ni `for`, ni heredocs `python3 - <<EOF`; la
  inspección va por `read`/`grep`/`edit`.
- **Denegado en todos los perfiles**: `git push`, `git merge` directo, `taiki
  confirm`, `taiki worktree merge` (salvo el conductor), `taiki transition` y
  writes de estado por CLI, `rm -rf`/`rm -r`, `sudo`, `git reset`/`git clean`/
  `git stash`, `git checkout --`, `chmod`/`chown` peligrosos, `bash`/`sh` y el
  staging masivo (`git add -A`, `git add .`, `git commit -a`). El conductor
  conserva la excepción consciente de `git add .opencode/*`.

La allowlist se evalúa **por segmento** y no es un sandbox: toda redirección
queda denegada, incluida `2>` (`2>/dev/null`, `2>&1`), junto con
`find -exec/-delete/-ok/-fprintf/-fls`, `sort -o` y `uniq IN OUT` (se escribe sin
redirección, con entrada por stdin). No hay atajos para `2>`: cualquier allow
que lo contenga reabre la escritura, porque `echo x > fichero 2>&1` casa con el
patrón y la última regla gana. Se mantienen las tuberías y los `&&` entre
comandos permitidos. Los workers no silencian stderr con redirección: si lo
necesitan, lo pide el conductor. La frontera real es el guard de tickets y la
revisión del conductor.

El límite de confianza es explícito: estas reglas evitan prompts repetidos y
reducen el daño accidental, **no son un sandbox de seguridad**. La garantía
sigue siendo el guard de tickets, la revisión del conductor y los gates.

### Borrado seguro

Los subagentes no borran por su cuenta. Si un cambio exige eliminar algo, el
worker publica un `checkpoint` o `comment` en el ticket con las rutas exactas
(sin globs ni directorios), el motivo y por qué no basta con dejarlo, y espera
el OK del conductor. El conductor **no borra**: valida que las rutas estén
dentro del worktree del worker y de los paths del ticket, que no sean ficheros
importantes o rastreados sin justificar y que no haya globs; si procede,
autoriza el borrado con `comment` (o `transition` si aplica) y lo registra en
el ticket; si no, lo deniega y explica el motivo. Nunca autoriza `rm -rf`,
`rm -r` ni rutas fuera del worktree.

Solo tras esa autorización el worker ejecuta un borrado acotado con `rm -f
<ruta>` o `git rm <ruta>` sobre la ruta autorizada. En los perfiles worker
`rm -f *` y `git rm *` están permitidos, pero siguen denegados `rm -rf*`,
`rm -fr*`, `rm -r*`, `sudo *` y los escapes fuera del worktree (`/*`, `../*`,
`~*` y `.worktrees/*`). El reviewer no borra: si detecta un archivo que
debería eliminarse, lo propone como hallazgo.

Routing de modelos: `documentacion` → `medium`; mantenimiento/funcionalidad →
`medium`; `bug`/`seguridad` → `high`. `worker-low` (16 pasos) queda para
ediciones cortas; la documentación grande va a `worker-medium`.
`recommend` y `assign` devuelven el mismo perfil: el MCP no recomienda un
worker distinto del que se lanza. Todos los perfiles worker usan
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

La rama `isolation.live_branch` del workspace (normalmente `main`) se permite
cuando todos los tickets del asunto usan aislamiento `none` o `scope`; un
ticket `worktree` sigue bloqueado con `protected-branch` y su trabajo va en
`agent/T-XXXX-*`.


## Aislamiento configurable

El aislamiento es por ticket (`--isolation`) y el defecto sale de
`.taiki/config.json`:

```json
{
  "isolation": {"default": "worktree", "live_branch": "main"},
  "scope": {"roots": ["."]}
}
```

| modo | escritura | commit | paralelismo |
|---|---|---|---|
| `none` | árbol vivo, sin acotar | rama viva (`live_branch`) | serial |
| `scope` | árbol vivo dentro de `paths` y `resources` | rama viva, índice acotado | sí, si no solapa |
| `worktree` | `.worktrees/T-XXXX`, rama `agent/T-XXXX-*` | en su rama, merge tras confirmar | sí, si no solapa |

- **Claim**: `none`/`scope` se reclaman sin worktree; `worktree` con
  `worktree=true`. Tras el claim, `session_move` deja la sesión en la ruta.
- **Schedule**: `none` va siempre a `serial`; `scope` y `worktree` admiten
  `parallel` si sus paths y recursos son disjuntos. Solapes, esquema, store
  core, config y merges son seriales.
- **Guard**: `scope` exige `paths` y falla cerrado sin ellos; solo permite
  escribir dentro de su alcance. `worktree` exige el worktree. `none` permite
  el árbol vivo pero no se paraleliza.
- **Commit live**: `taiki commit` en `none`/`scope` valida el índice contra
  `paths` y commitea en la rama viva. `taiki commit --repo ~/.config --ticket
  T-XXXX` commitea el árbol vivo de otro repositorio (dotfiles) y enlaza
  `external <repo>@<sha>`: la DB y el ticket no cambian de workspace. Los
  `paths` absolutos del ticket (`~/.config/hypr/`) se traducen al root del
  repo live antes de validar el índice; no dupliques la ruta del `--repo`.
  Si un path declarado cae en `.gitignore`, el comando avisa con la ruta y
  sugiere `git add -f <ruta>` o una excepción `!ruta/`: taiki no stagea por ti
  y, sin el aviso, el commit acababa en «Nada en el índice».
- **Merger**: el subagente `merger` solo existe en workspaces `worktree`: el
  registro de agentes y `taiki mcp install` lo omiten en `none`/`scope` (y
  retiran un `merger.md` de Taiki que hubiera quedado, solo si su contenido es
  idéntico al de origen; uno personalizado se conserva). Un workspace live no
  integra worktrees; el conductor no debe lanzarlo.
- **Perfiles**: `mcp install` es no destructivo: copia los perfiles que faltan y
  conserva los que difieren del paquete (los lista como personalizados y avisa
  por stderr). `--force-agents` los sobrescribe a propósito. Si el paquete no
  trae `.opencode/agents/`, avisa y deja los existentes.
- **Reversión**: `taiki update --reversal '{"kind","ref","command"}'`.
  `reject --revert` (usuario) ejecuta `git revert` solo para `git-revert`; en
  `copy`/sistema imprime el comando y cancela el ticket. `worktree` no aplica.
- **Riesgo**: `risk` (`low`/`medium`/`high`) se deriva de paths y resources.
  Rutas sensibles marcan `requires_review` como aviso, no bloquean
  `verificado`: `taiki confirm` es la revisión especial.
- **Autorización**: el guard bloquea sin `taiki authorize` (solo usuario)
  `sudo`, `systemctl` de sistema (sin `--user`), `pacman`/`paru`/`yay` y
  escrituras en `/etc`, `/usr`, `/boot` o `/var/lib`.

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
- **Worktree obligatorio en modo `worktree`**: si otra sesión está activa, el
  guard bloquea las escrituras dentro del checkout cuando tu ticket no tiene
  worktree o sale de su `scope`; la lectura sigue libre. Escape consciente:
  `TAIKI_SHARED_TREE=1`.
- **Commits acotados**: declara las rutas del ticket con `--paths` y usa
  `taiki commit -m "..."`. Rechaza el índice fuera de alcance o que solapa con
  otro ticket activo; el hook `pre-commit` (`taiki hook-check-index`) aplica la
  misma regla y exige `TAIKI_TICKET` si hay varios tickets activos.
- **Merge asistido**: el usuario confirma; solo después el conductor mergea en
  orden de dependencias. `taiki worktree merge T-XXXX` hace rebase sobre
  `orchestrator.integration_branch` (`develop` por defecto), ejecuta
  `tests/run.py --strict-budget` si existe (`--no-tests` para omitir),
  `merge --ff-only` y limpia con `--delete`. El presupuesto de la suite es
  global (techo de comprobaciones y tiempo de toda la suite, no por módulo):
  el merge usa el mismo gate que la CI, así que si la acumulación de checks
  deja la suite por encima del techo el merge falla ahí y no mergea nada.
  Nunca mergea si algo falla. Si `develop` no existe,
  avisa y usa la rama actual sin romper. Exige que el ticket esté
  `confirmado`: el commit vive en el worktree, pero la rama de integración solo
  recibe lo que el usuario ya revisó. `main` no se toca aquí: solo la release
  (`taiki release`) escribe en ella y después se publica con
  `git push origin main --follow-tags`. `--force` es el bypass consciente.
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
# solo ahora se fusiona la rama en develop (o en la rama de integración)
taiki worktree merge T-0001 --delete
# main no se toca aquí: solo cambia con una release
taiki release --push
```

`taiki release --push` publica en un solo comando, en este orden: sube
`develop` si solo iba por delante, ejecuta los gates, aplana `develop` en un
commit de `main`, crea el tag, empuja `main` y el tag **juntos** (`git push
--atomic`), reconcilia `develop` con `main` y vuelve a subir `develop`. Cada
paso aparece en `pushed` (también en `--json`). Si `develop` va por detrás o
divergida aborta antes de escribir con el comando exacto; si el remoto rechaza
`main` (protección), para sin reconciliar y deja el release en local con el
paso y el comando de reintento. `--push` es incompatible con `--dry-run` y
exige un remoto configurado.

El **modo local** (sin `--push`) no toca la red: úsalo para ensayar con
`--dry-run`, en repos sin remoto o para revisar el commit de `main` antes de
subirlo a mano:

```bash
taiki release
git push origin main --follow-tags
git push origin develop
```

`taiki release` aplana `develop` en un commit de `main`, crea el tag y después
**reconcilia** `develop` con `main` (`git merge --no-ff`, idempotente y sin
reescribir `main`), restaurando la rama en la que estabas. Sin ese paso la
segunda release fallaría porque el commit del squash no existe en `develop`.
El resultado incluye `reconciled` y `reconcile_command` (también en `--json`);
si el merge conflictúa, la release ya está publicada, se aborta el merge y se
informa `reconcile_error` para resolverlo a mano. Para deshacer una release,
`git revert <commit-de-release>` en `main`; el merge de reconciliación de
`develop` no estorba y solo se revierte con
`git revert -m 1 <commit-de-reconciliación>` si hace falta.

La release también registra el commit aplanado en `.taiki/releases.json`
(**versionado**), con `{tag, version, released_sha, date}` por entrega. El
registro va dentro del merge de reconciliación de `develop` o, si no hizo falta
merge, en un commit `chore(release): registrar vX.Y.Z`; `main` no recibe ningún
commit extra por esto. La siguiente release calcula los tickets desde el
`released_sha` de la última entrada, no desde el tag squash, así que no repite
commits ya publicados ni fuerza un bump por un `feat` antiguo. En repos sin
marcador se usa el rango `<tag>..develop` y, si existe, el primer padre del
merge `chore: reconciliar … tras <tag>`. Si el rango no tiene commits
publicables, la release aborta con *«No hay commits nuevos desde vX.Y.Z»*.

`git push origin main --follow-tags` publica la release en modo local (con
`--push` ya se publica dentro del comando). El hook `pre-push`
solo deja empujar `main` cuando el tip es una release de `taiki release`
(asunto `chore(release): vX.Y.Z` y/o tag `v*` que lo apunte) y, en ese caso,
se limita a comprobar que los tickets del rango existan; un commit suelto en
`main` sigue bloqueado. No uses `--no-verify` para saltarte el hook: si el
push se rechaza, publica con `taiki release` o deshaz la release con
`git revert` en `main`.


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


## Workspace de sistema (`taiki sys`)

El trabajo de sistema no vive en el repo del proyecto: usa un workspace propio,
un repositorio Git en `~/.local/share/taiki-sys` (configurable con `TAIKI_DB` o
ejecutando desde su raíz) con su `.taiki/`, su DB local y su rama viva. Los
ficheros vivos del sistema siguen siendo la fuente de verdad; el repo es un
ledger de snapshots y registros.

```text
~/.local/share/taiki-sys/
├── .taiki/                 # config, DB local y archivos versionables
├── snapshots/<T-XXXX>/<timestamp>/…
└── records/
    ├── tickets.json
    ├── tickets.md
    └── manifest.json
```

- **`taiki sys status`**: repo Git (raíz, rama, cambios), `scope.roots`,
  `isolation` (`default` y `live_branch`), `verify.command` y dotfiles
  detectados.
- **Raíces**: `scope.roots` se compone de `config.json` y `local.json`; las
  rutas relativas se resuelven contra el workspace. `taiki sys snapshot` rechaza
  ficheros fuera de esas raíces.
- **`taiki sys snapshot T-XXXX --path FILE`**: copia a
  `snapshots/T-XXXX/<timestamp>/<ruta>` con `shutil.copy2`, calcula SHA-256,
  commitea, **enlaza el commit al ticket** (`commit`, actor `agente`, sin
  resolver en el repo del proyecto: el sha vive en el repo de sistema) y anota
  la reversión `copy` con el comando `cp -a`. Ese enlace es la evidencia que
  `complete` exige en `none`/`scope`: no hace falta `taiki link` a mano.
  Solo para tickets `none`/`scope` y ficheros existentes.
- **`--packages`**: guarda `pacman -Qqe` en
  `snapshots/T-XXXX/pacman-Qqe.txt`; `--from FILE` permite leer una lista sin
  ejecutar `pacman`. También enlaza su commit al ticket. El rollback es
  **manual**: `pacman -S --needed $(cat …)`.
- **Dotfiles**: si el fichero vive en el repo de dotfiles detectado, no se
  copia. Se registra una reversión `git-revert` en ese repo y se imprime el
  comando `taiki commit --repo ~/.config --ticket T-XXXX`, con los `paths`
  absolutos del ticket (`~/.config/hypr/`) traducidos al repo live. El commit
  de dotfiles se enlaza como `external <repo>@<sha>`.
- **`taiki sys export [T-XXXX]`**: exporta los tickets y escribe
  `records/tickets.json`, `records/tickets.md` y `records/manifest.json` con
  snapshots, hashes y enlaces (`commit` y `external`), y lo commitea. Con
  código, enlaza el commit de `records/` al ticket, así que tampoco necesita
  `taiki link` manual. La DB nunca se versiona.
- **Enlace manual**: solo para cambios **sin commit** propio del ledger, por
  ejemplo un fichero nuevo sin snapshot o trabajo hecho a mano en el árbol
  vivo: `taiki link T-XXXX commit <sha>` o `external repo@sha`. Snapshot y
  export ya enlazan solos.
- **`taiki sys audit`**: compara los hashes del manifiesto con los ficheros
  actuales. Marca `changed`/`missing` y si el cambio está cubierto por un
  ticket abierto (`snapshots/<T-XXXX>/…`). **Límite**: no escanea ficheros
  nuevos; solo verifica los paths registrados en el manifiesto (follow-up).
- **`verify.command`**: comando de verificación del workspace
  (`.taiki/config.json`). `taiki sys status` lo muestra; no se ejecuta solo.

### Arch y systemd

- Paquetes: `pacman -Qqe` es la foto; la instalación la ejecuta el usuario tras
  `taiki authorize` (el guard bloquea `pacman`, `paru` y `yay` sin autorización).
- Servicios de usuario: `--resource systemd:user/<unit>` queda dentro del
  alcance de `scope`; los de sistema usan `systemd:system/<unit>` y elevan el
  `risk` a `high`.
- `systemctl --user …` no exige autorización; `systemctl` sin `--user` sí, y se
  bloquea hasta `taiki authorize`.
- Los snapshots de `/etc`, `/usr`, `/boot` y `/var/lib` elevan el riesgo; no
  sustituyen a un backup del sistema.

### Riesgos

- El workspace de sistema escribe en rutas reales: un snapshot no revierte
  paquetes ni servicios por sí solo.
- `sys audit` no detecta ficheros nuevos ni cambios fuera del manifiesto.
- La autorización es por ticket y **solo la da el usuario**; los agentes deben
  pedirla y no pueden auto-revisarse ni auto-confirmarse.
- La DB del workspace de sistema no se versiona: `sys export` es el respaldo
  lógico de los tickets.

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


## Rendimiento y contexto

Línea base medida antes de v0.3.0 (T-0058.06, 2026-11-03, base `05141b9`).
Todo es local y sin red; el tiempo usa reloj monótono y medianas. Los números
dependen de la máquina, así que sirven para comparar la misma estación y para
detectar saltos, no como valor absoluto.

| Métrica | Valor | Objetivo | Comando |
| --- | --- | --- | --- |
| `tools/list`: tools / bytes | 32 tools / 30.734 bytes (30,0 KiB) | ≤ 32 KiB | `python3 -m tests.benchmarks` |
| Arranque del CLI (`list`) | 201 ms (mediana de 3) | ≤ 300 ms | `python3 -m tests.benchmarks` |
| Suite `--strict-budget` | 1.968 comprobaciones / 10,86 s | ≤ 11 s (presupuesto 11,6 s) | `python3 tests/run.py --strict-budget` |
| Brief | 2.443 chars (54 % de 4.500) | ≤ `brief_budget_chars` | `wc -c .taiki/generated/brief.md` |
| Contexto de docs | `docs/memoria.md` 610 líneas; `AGENTS.md` 234 | informativo | `wc -l docs/memoria.md AGENTS.md` |

- `tests/benchmarks.py` imprime la foto de contexto junto a los benchmarks de
  connect/export; `--verify` ya lo ejecuta. El catálogo se serializa compacto
  (sin indentación) desde `mcp_layer.schemas.TOOLS`, que es lo que viaja en
  `tools/list`.
- El arranque mide `python3 cli.py list` contra una DB temporal vacía: `list`
  necesita DB y así la medición no depende de los datos del proyecto.
- El brief solo se mide si `.taiki/generated/brief.md` existe (está ignorado
  por Git); en un worktree recién creado aparece como «sin generar».
- Umbrales de decisión: `tools/list` > 32 KiB, arranque > 300 ms o suite
  `--strict-budget` > 11 s. Si se supera alguno, se abre una tarea hija de
  T-0058 con la métrica y la fecha, y se decide con datos; no se optimiza a
  ciegas. El benchmark avisa pero no falla por tiempo: es dependiente de la
  máquina y no debe bloquear el gate.
- No se tocó código de producción: las tres métricas están dentro de objetivo.


## Notas de diseño

- El estado es un contrato de flujo, no una frontera de seguridad: un agente con
  acceso al shell podría ejecutar `taiki confirm`. La separación evita errores
  accidentales, no un atacante.
- SQLite en modo WAL con `busy_timeout` y transacciones `BEGIN IMMEDIATE` reentrantes permite alta concurrencia segura sin condiciones de carrera.
- Resolución de ámbito inteligente: `resolve_scope` localiza la raíz del repositorio Git hacia arriba para asociar el proyecto correcto incluso al ejecutar desde subdirectorios.
- Los códigos (`T-0001`) son legibles y estables; los ids internos no se exponen en el flujo normal.
