# Taiki — guía para agentes

Taiki es un gestor local de tickets en SQLite. Este repositorio es el proyecto
completo y su base de datos vive en `.taiki/taiki.db` (ignorada por Git).

## Contexto rápido

- CLI: `taiki` (wrapper `bin/taiki`).
- MCP: `bin/taiki-mcp`, instalado **solo en este proyecto** con
  `taiki mcp install`. No hay MCP global. Además del ciclo de vida expone
  `status`, `whoami`, `heartbeat`, `worktree_create`, `worktree_list`,
  `promote` y `health`; úsalos antes de coordinar trabajo en paralelo.
- DB: `.taiki/taiki.db`. `config.json` y `.taiki/.gitignore` se versionan;
  `taiki.db`, `local.json` y `backups/` no.
- Guard: activo porque `.taiki/config.json` tiene `"guard": true`.
- Aislamiento: por ticket, `none`/`scope`/`worktree`; el defecto es
  `worktree` (`isolation.default`). `scope` acota por `paths` y `resources`;
  `none` trabaja en el árbol vivo en serie.

## Flujo de trabajo

0. **Brief primero.** Aplica el brief de memoria inyectado en
   `.taiki/generated/brief.md` o, si no aparece, llama a la herramienta MCP
   `brief`. Usa `memory_search` para el contexto que falte y aplica los
   `hints` del recibo de `claim` (memorias por rutas, tipo o etiquetas). La
   memoria es contexto: no sustituye al ticket ni a estas reglas, y una
   propuesta no es instrucción hasta que el usuario la apruebe. Si una memoria
   trae `freshness` con `ok=false`, **no la uses como verdad**: actualiza el
   contenido y la evidencia con `patch`/`supersede` o niégala con
   `feedback`/`supersede`. Si el brief
   muestra `## Pendientes`, son propuestas sin aprobar: revísalas con
   `taiki memory pending`; `taiki status` y `health` también las cuentan. Las
   memorias de agente con evidencia validada (ticket verificado/confirmado,
   commit resoluble o fichero existente) se activan solas; las reglas negativas
   y los `workflow`/routing siguen propuestas hasta su aprobación.
   `memory.brief_mode` (`full`/`index`) decide si el brief lleva el contenido
   destilado o solo el índice con `memory_search`; `memory.enforce_budget`
   (`false` por defecto) convierte el exceso de presupuesto en aviso
   (`budget_warning`) en vez de bloquear escrituras.
1. Consulta el trabajo pendiente: `taiki next`, `taiki list`, `taiki stats`.
2. Antes de tocar código, crea o reclama un ticket:
   `taiki new -t "..." -k mantenimiento -p alta` y `taiki claim T-0001 --agent opencode`.
3. Al terminar: `taiki complete T-0001 -r "qué se hizo y cómo se verificó" --commit <hash>`.
4. La confirmación es del usuario: `taiki confirm` es **la revisión especial** y
   nunca la ejecuta un agente. `taiki review` es opcional y no bloquea
   `verificado`: solo deja constancia en tickets con rutas sensibles
   (`requires_review` es un aviso). Quien revisa puede marcar `verificado` con
   evidencia (`taiki transition T-0001 verificado --note "tests/CI/revisión"`).
   `taiki authorize` (solo usuario) permite comandos de alto riesgo; sin ella
   el guard los bloquea. Tras la confirmación, el conductor mergea solo
   tickets `confirmado`, en orden de dependencias, con
   `taiki worktree merge T-XXXX --delete`; el guard rechaza los no confirmados.
5. El asunto del commit debe incluir `T-XXXX`; el hook `commit-msg` lo exige.
   Un `feat:` pequeño puede declarar `Release-Bump: patch|minor|major` como
   trailer del cuerpo; sin trailer, `feat` sube minor.
6. **Captura con evidencia.** Si hubo corrección, rework o un workaround no
   obvio, propón una lección con la herramienta MCP `memory_write` y evidencia
   (`ticket`, `commit`, `file`, `url` o `session`). No guardes fallos
   transitorios, credenciales, narrativas ni prohibiciones amplias. La
   aprobación es del usuario con `taiki memory approve`; los agentes no
   aprueban propuestas.
7. **Revisión periódica con curator.** Cada 5 tickets verificados/cerrados o
   cuando `memory_candidates(mode=list)` acumule 3 o más candidatos, el
   conductor lanza el subagente `curator` en background con el paquete
   `memory_candidates(mode=review)`. El curator propone como máximo 5 memorias,
   prefiere `patch`/`supersede` sobre `add`, exige evidencia, no aprueba, no
   edita código y no lanza subagentes. El conductor reporta las propuestas
   pendientes (visibles en el brief, `taiki status` y `health`); **los agentes
   proponen y el usuario aprueba o rechaza** con `taiki memory approve` o
   `taiki memory reject`. Los agentes nunca aprueban ni rechazan.
8. El conductor y los subagentes viven en `.opencode/agents/`; consulta el
   routing de variantes y el presupuesto de concurrencia con la herramienta
   MCP `agents` y `schedule` (paralelismo seguro por paths). Los perfiles
   worker usan deepseek-v4.1-flash; `worker-medium` va sin variante. La
   documentación grande va a `worker-medium`; `worker-low` (16 pasos) queda
   para ediciones cortas. Si un worker agota pasos o falla, un reintento con
   el perfil superior y, si vuelve a fallar, bloquear: no se escala en bucle.
   Los agentes escriben estado por MCP (claim/complete/transition/checkpoint)
   para conservar actor, actor_type y owner_session; el CLI es del usuario.
   Tras claim con worktree, usa `session_move` a la ruta devuelta. El shell
   del conductor tiene los mismos privilegios que el agente `build` de OpenCode
   (fallback `allow`) con un guardrail de denies explícitos; los workers y el
   reviewer usan fallback `deny`: solo ejecutan su lista blanca (inspección,
   edición común, `git show`, lecturas de taiki y `taiki commit`) y, si
   necesitan algo más, publican `checkpoint`/`comment` con el comando exacto y
   el conductor lo ejecuta o autoriza. Nunca piden permiso al usuario. Siguen
   denegados push, merge directo, confirm, `rm -rf`, `sudo`, `bash`/`sh` y los
   writes de estado por CLI. Los workers sí pueden `git rebase` (incluidos
   `--continue`, `--abort` y `--skip`), pero no `-i` ni `--exec`: sin TTY se
   cuelgan; los conflictos se resuelven con `edit`, `git add` y `--continue`.
   No es un sandbox: la garantía
   es el guard, la revisión y los gates. `uvx` solo ruff/mypy. El smoke opt-in
   es `python3 tests/live_orchestrator.py`.
   Merge de la épica: .03 → .04 → … → .13. `taiki status`
   muestra la cola (listos, en curso, verificando, bloqueados) y métricas.
   Una dependencia solo se cierra al `verificado`/`confirmado`, no al
   `completado`.

## Playbooks, routing y revisión

- **Playbooks**: procedimientos con evidencia y apoyos en `references/`,
  `scripts/`, `assets/` o `templates/`. Se gestionan con
  `taiki memory playbook add|list|show|export|archive`, se exportan a
  `.taiki/generated/skills/<slug>/SKILL.md` y se cargan bajo demanda con
  `memory_playbook(slug)` o `taiki://playbook/{slug}`. El brief solo muestra
  su índice. No ejecutes los scripts de apoyo.
- **Pantalla Memoria (TUI)**: la tecla `Y` abre propuestas, activas, stale,
  playbooks y presupuesto. `a` aprueba y `x` rechaza (con motivo) propuestas:
  son acciones del usuario, nunca automáticas. `p` fija o desfija, `r` archiva
  con confirmación y `o` exporta el playbook. Nunca borra: archivar conserva
  el histórico en el ledger.
- **Skills curadas (AutoSkills)**: `taiki skills suggest|list` son de lectura;
  `install` y `remove` son **solo del usuario/CLI** y no se exponen por MCP
  (allí solo existe `skills_suggest`, read-only). `install` descarga con
  SHA-256 y deja un playbook `proposed`: no lo apruebes tú. `remove` archiva y
  `sync` exporta activos. No hay Node en el núcleo ni ejecución de scripts.
- **Routing advice**: `schedule` y `agents(kind=...)` incluyen `advice` con el
  perfil observado, variante, steps, muestra, first-pass, Wilson e intentos
  medios (mínimo 5 muestras). Es una sugerencia: nunca cambia la configuración
  ni lanza agentes; decide el conductor.
- **Reviewer**: antes de revisar usa
  `memory_checklist(kind="bug"|"funcionalidad", memory_kind="pitfall"|"convention", paths=[...])`:
  `kind` es el tipo de **ticket** y filtra por `ticket_kinds` (las memorias sin
  etiqueta son generales), mientras que `memory_kind` es el tipo de **memoria**.
  Completa con `memory_search` de pitfalls. Si detecta un fallo recurrente,
  propone un pitfall sin aprobarlo.
- **Curado y portabilidad**: `taiki memory curate` caduca, archiva y deduplica
  sin borrar ni aprobar; `pin`/`lock` quedan exentos y `--dry-run` no escribe.
  `auto_curate` es opt-in y se dispara desde `status`, `next` o `memory curate`.
  Para versionar o mover memoria usa `memory export`/`import` (JSON/Markdown,
  checksum, estrategias y `--dry-run`). `memory rollback` revierte por asiento
  de ledger o por snapshot y deja auditoría. `memory stats` e `impact` dan
  tendencias descriptivas: **no afirmes causalidad**.

## Aislamiento configurable

Cada ticket declara cómo escribe. El modo se fija con `--isolation` en
`taiki new`/`taiki update` y el defecto es `isolation.default` del
`.taiki/config.json` (`worktree`).

| modo | dónde escribe | paralelismo |
|---|---|---|
| `worktree` (defecto) | `.worktrees/T-XXXX` con rama `agent/T-XXXX-*` | igual que `scope`: por `paths`/`resources` disjuntos |
| `scope` | checkout vivo, solo dentro de `paths` y `resources` | sí, si no solapan |
| `none` | checkout vivo, sin acotar | **serial**: nunca en paralelo |

- **`claim`**: con `worktree=true` crea y enlaza el worktree; en `none`/`scope`
  se reclama sin worktree y se trabaja en el árbol vivo. Tras el claim, la
  sesión se mueve con `session_move` a la ruta indicada.
- **`schedule`**: devuelve `parallel` (paths y recursos disjuntos) y `serial`
  (solapes, esquema, store core, config, sin paths o `none`). `none` siempre es
  serial; `scope` y `worktree` admiten paralelo si no comparten paths ni
  recursos. Las migraciones y los merges nunca van en paralelo.
- **Guard**: en `scope` exige `paths` y solo permite escribir dentro de ellos
  (fail-closed si faltan); en `worktree` exige el worktree; en `none` permite
  el árbol vivo pero serializa. `TAIKI_SHARED_TREE=1` sigue siendo el escape
  consciente.
- **Commit live**: en `none`/`scope` el commit va a la rama viva
  (`isolation.live_branch`, `main` por defecto) con el índice acotado por
  `paths`. `taiki commit --repo <ruta> --ticket T-XXXX` commitea el árbol vivo
  de **otro** repositorio (dotfiles) y enlaza `external <repo>@<sha>`; la DB y
  el ticket siguen en el workspace actual.
- **Reversión**: `taiki update --reversal '{"kind","ref","command"}'`.
  `taiki reject --revert --why ...` (solo usuario) hace `git revert` cuando la
  reversión es `git-revert`; en `copy`/sistema solo imprime el comando manual.
  Un ticket `worktree` no se revierte así: se rechaza normal.

## Agente de sistema (`taiki sys`)

El trabajo de sistema vive en un workspace propio, `~/.local/share/taiki-sys`
(repositorio Git con su `.taiki/` y su DB local). Los ficheros vivos siguen
siendo la fuente de verdad; el repo guarda snapshots y registros.

- `taiki sys status`: repo, rama, cambios, raíces de `scope.roots`,
  aislamiento, `verify.command` y dotfiles detectados.
- `taiki sys snapshot T-XXXX --path /etc/foo.conf`: copia a
  `snapshots/T-XXXX/<timestamp>/`, commitea y anota la reversión en el ticket.
  Solo para tickets `none`/`scope`. `--packages` guarda `pacman -Qqe` (rollback
  manual); `--from FILE` evita ejecutar `pacman`.
- `taiki sys export [T-XXXX]`: escribe `records/tickets.json`, `tickets.md` y
  `manifest.json` (snapshots, commits y enlaces `external`) y lo commitea. La
  DB no se versiona.
- `taiki sys audit`: compara los hashes del manifiesto con los ficheros
  actuales y marca los cambios cubiertos por un ticket abierto. **No escanea
  ficheros nuevos**: solo los paths registrados.
- Si el fichero vive en el repo de dotfiles, `sys snapshot` no lo copia:
  indica `taiki commit --repo <dotfiles> --ticket T-XXXX` y la reversión es un
  `git revert` de ese commit.

`risk` (`low`/`medium`/`high`) se **deriva** de `paths` y `resources`, no se
declara. Rutas sensibles marcan `requires_review` como aviso. El guard bloquea
sin `taiki authorize` los comandos de alto riesgo: `sudo`, `systemctl` de
sistema (sin `--user`), `pacman`/`paru`/`yay` y escrituras en `/etc`, `/usr`,
`/boot` o `/var/lib`. Solo el usuario autoriza o revisa.

## Agentes en paralelo

Varias sesiones pueden trabajar sobre el mismo proyecto. Para no pisarse:

- **Reclama antes de escribir** y mantén **un solo ticket activo por sesión**.
  Si otra sesión ya tiene el ticket, no lo reclames a la fuerza.
- **Un worktree por escritor** cuando el ticket usa `worktree` (el defecto).
  Vive en `.worktrees/<T-XXXX>`, ignorado por Git, con la DB compartida de
  `.taiki/`. El worktree puede contener el `.taiki/config.json` versionado, pero
  `taiki` resuelve siempre la DB del checkout principal; no crees una DB local
  dentro de `.worktrees/`. Con `scope`/`none` se escribe en el árbol vivo:
  `scope` solo dentro de `paths`/`resources` y `none` en serie. Las lecturas
  pueden compartir el árbol. Si otra sesión está activa, el guard **bloquea
  escribir en el checkout** fuera de tu scope o sin worktree; el escape
  consciente es `TAIKI_SHARED_TREE=1`.
- **Al reclamar**: `taiki claim T-XXXX --worktree` crea el worktree y lo enlaza
  (o reclama sin él en `none`/`scope`). Durante comandos largos,
  `taiki heartbeat T-XXXX` renueva el lease.
- **Al terminar**: commitea en el worktree, marca el ticket `completado` y
  espera la confirmación del usuario. Solo después, `taiki worktree merge
  T-XXXX --delete` hace rebase sobre la rama de integración (`develop`), tests,
  `merge --ff-only` y limpia. El conductor
  mergea solo tickets `confirmado`, en orden de dependencias; el guard rechaza
  los no confirmados y `--force` es el bypass consciente. La confirmación sigue
  siendo del usuario: `taiki confirm` retira solo los worktrees limpios y
  mergeados (`--keep-worktrees` para conservarlos).
- **El merger integra solo tickets confirmados.** Tras la confirmación del
  usuario, el conductor le pasa una lista ordenada por dependencias y el merger
  ejecuta `taiki worktree merge T-XXXX --delete` uno a uno, comprobando antes el
  estado `confirmado`, el worktree limpio y la rama `agent/T-XXXX-*`. Se detiene
  en el primer fallo (sin `--force` ni `--no-tests`), no edita, no confirma, no
  hace push y no borra; el conductor revisa su informe y corre la verificación
  final.
- **Ramas**: el trabajo diario vive en `agent/T-XXXX-*` y se integra con
  `--ff-only` en `develop`; `main` solo recibe releases unificadas y
  etiquetadas. Ciclo, release, rollback y versionado:
  [docs/desarrollo.md](docs/desarrollo.md#ramas-y-releases).
- **Stagea rutas concretas** (`git add <ruta>`). Si otra sesión tiene un ticket
  activo, el guard bloquea `git add -A`, `git add .`, `git commit -a`,
  `git stash`, `git reset`, `git clean` y `git checkout -- .`.
- **Borrado seguro**: los subagentes no borran por su cuenta. Si un worker
  necesita eliminar algo, publica un `checkpoint`/`comment` con las rutas
  exactas (sin globs) y el motivo, y pide OK al conductor; este valida que estén
  dentro del worktree y de los paths del ticket, que no sean ficheros
  importantes/rastreados sin justificar y lo autoriza en el ticket. Solo
  entonces el worker ejecuta `rm -f <ruta>` o `git rm <ruta>` sobre la ruta
  autorizada; `rm -rf`, `rm -r`, `sudo` y lo que quede fuera del worktree siguen
  denegados. El conductor no borra (solo autoriza) y el reviewer tampoco.
- **Nunca uses `git stash` a pelo**: el stash es único para todos los worktrees
  y puede aplicarte cambios de otra sesión. Commitea el WIP en tu rama.
- **Commitea solo lo tuyo** con `taiki commit -m "..."`: respeta las rutas del
  ticket (`taiki new --paths src/`) y rechaza el índice que solapa con otro
  ticket activo. Con varios tickets activos el `pre-commit` exige
  `TAIKI_TICKET=<código>`. Fusiona de una rama a la vez.
- Si el guard bloquea y no hay conflicto real, el bypass manual es
  `TAIKI_GUARD=off`.

## Coste de contexto

- `list` ya devuelve 20 abiertos en resumen; `get` acota historial/enlaces y
  las mutaciones devuelven un recibo. Usa `detail=true` solo cuando necesites
  el ticket completo y `get raw=true` para el registro íntegro.
- En CLI prefiere `taiki list --json-summary --compact --limit 20` y
  `taiki show T-XXXX --json --compact`; evita `list --json` completo.
  `--compact` y `--json-summary` ya implican `--json`.

## Política de tests

- **Un comportamiento, un test en la capa más baja.** La lógica se prueba en
  `ticketlib`/`store`; MCP y CLI solo comprueban contrato (entrada/salida);
  la TUI solo lógica pura (estado, detalle, mapa), nunca curses.
- **Un bug, un test de regresión** en la capa que falló. No se copia el mismo
  caso en tres capas.
- **Borrar es válido.** El presupuesto de comprobaciones es un **techo**, no un
  objetivo: bajar checks no avisa si la cobertura se mantiene. Añadir por encima
  del techo obliga a podar o justificarlo.
- **El tiempo y la cobertura son gates duros en CI.** Local avisa; CI falla con
  `--strict-budget` y con los umbrales por capa de `tests/coverage_gate.py`.
- **Poda periódica:** shims sin uso, módulos de más de 700 líneas y matrices
  repetidas (transiciones, enums, filtros) se convierten en tablas.

## Comandos útiles

```bash
taiki init                  # DB y workspace actuales
taiki stats                 # resumen
taiki brief                 # brief operativo de la memoria
taiki memory search <texto> # búsqueda puntual sin cargar el brief entero
taiki remember "texto"      # preferencia activa del usuario
taiki memory pending        # propuestas pendientes de aprobación
taiki memory pending --diff # diff frente a la memoria sustituida
taiki memory candidates     # candidatos deterministas del histórico
taiki memory review --write # paquete para el curator
taiki memory curate         # caducidad, archivo y dedupe (nunca borra)
taiki memory stats          # estado, presupuesto, uso y routing
taiki memory impact         # tendencias, sin causalidad
taiki memory export FILE    # snapshot JSON/Markdown con checksum
taiki memory import FILE    # importa con keep/replace/new y --dry-run
taiki memory rollback ID    # revierte un asiento; --snapshot para un archivo
taiki memory approve M-0001 # solo el usuario
taiki memory playbook list  # playbooks y su export
taiki memory playbook export --all  # .taiki/generated/skills
taiki skills suggest                # skills curadas para el stack
taiki skills install <nombre>       # solo usuario; deja un playbook propuesto
taiki skills list / remove / sync   # gestión y exportación
taiki tui                   # dashboard del proyecto
taiki mcp install           # MCP de OpenCode solo para este proyecto
taiki mcp uninstall         # lo retira y desactiva el guard
taiki servers               # instancias MCP vivas (registro global)
python3 tests/run.py          # suite completa
python3 tests/run.py --verify # todos los gates (suite, cobertura, ruff, mypy, benchmarks)
python3 tests/run.py --audit  # ruido, presupuesto y línea base
```

## Aislamiento

- No uses la DB de `~/.config/.taiki`: es el workspace del proyecto de dotfiles.
- `TAIKI_DB` o `--db` permiten apuntar a otra DB explícitamente.
- Cada proyecto arranca su propia instancia MCP; el registro de procesos en
  `~/.local/share/taiki/registry.db` es solo inventario, no contiene tickets.
