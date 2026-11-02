# Desarrollo

[← README](../README.md) · [Índice de documentación](README.md)

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

## Ramas y releases

`main` es la historia publicada y solo contiene releases; `develop` es la rama
de integración donde aterriza el trabajo diario. Ningún cambio llega a `main`
sin pasar por una release verificada.

| Rama | Origen | Destino | Uso |
|---|---|---|---|
| `main` | — | — | Solo releases: un commit unificado y etiquetado por entrega. |
| `develop` | `main` (una vez) | `main` vía release | Integración diaria de las ramas de ticket. |
| `agent/T-XXXX-<slug>` | `develop` | `develop` | Rama efímera de un ticket y su worktree. |
| `release/x.y.z` | `develop` | `main` y `develop` | Congelación opcional antes de publicar. |
| `hotfix/T-XXXX` | `main` | `main` y `develop` | Corrección urgente de una release publicada. |

`taiki worktree merge` integra en `orchestrator.integration_branch` (`develop`
por defecto); las ramas `agent/*` nunca se mergean directamente en `main`.

### Ciclo de un ticket

1. El worker reclama el ticket con worktree: la rama `agent/T-XXXX-<slug>` nace
   de `develop` (o de la rama de integración configurada).
2. Commits pequeños y convencionales; rebase frecuente sobre `develop`.
3. Antes de pedir revisión, `python3 tests/run.py --verify` debe estar en verde
   (suite, cobertura, ruff, mypy y benchmarks).
4. El conductor revisa el diff y los gates; el usuario confirma el ticket.
5. `taiki worktree merge T-XXXX` hace rebase, `merge --ff-only` y limpia la
   rama y el worktree. El historial de `develop` queda lineal, sin merge
   commits.

`release/x.y.z` solo se usa si hace falta congelar: se crea desde `develop`, se
estabiliza allí y se integra de vuelta en `develop` antes de publicar.

### Release

Desde una `develop` limpia, al día y con la CI en verde:

```bash
git switch develop && git pull --ff-only
python3 tests/run.py --verify
taiki release
```

`taiki release` aplana los commits de `develop` en **un solo commit** sobre
`main`, actualiza `CHANGELOG.md` a partir de los tickets y commits desde el tag
anterior y crea el tag anotado `vX.Y.Z`. `main` no recibe pushes ni commits
directos: la única vía es ese comando con los gates en verde.

### Reglas

- `main` protegida: sin push directo, sin commits sueltos y sin reescribir
  historia publicada. `develop` también se protege contra force-push.
- Commits convencionales (`feat:`, `fix:`, `docs:`, `refactor:`, `test:`,
  `chore:`) con el código `T-XXXX` en el asunto; el hook `commit-msg` lo exige.
- Ramas cortas, rebase antes de integrar, `--ff-only` y borrar la rama al
  fusionar.
- Nunca uses `git stash` compartido entre worktrees: commitea el WIP en tu rama.
- Un merge de una rama a la vez; el guard y los hooks rechazan el trabajo
  directo en `main` y las ramas desactualizadas (ver
  [operación](operacion.md#agentes-en-paralelo-worktrees)).

### Rollback

No se reescribe `main`. Para deshacer una release:

```bash
git switch main && git pull --ff-only
git revert <commit-de-release>   # commit inverso; la historia queda intacta
```

Si el fallo afecta a una release ya publicada, se prepara un `hotfix/T-XXXX`
desde `main`, se etiqueta una versión de parche y se integra también en
`develop`. El tag anterior sigue siendo el punto de retorno para desplegar.

### Versionado y CHANGELOG

Versionado semántico: un cambio incompatible sube **major**, un `feat:` sube
**minor** y el resto (`fix:`, `docs:`, `refactor:`…) sube **patch**. El
`CHANGELOG.md` se genera durante la release agrupando los tickets y commits
desde el tag anterior; solo se edita a mano para corregir el resumen.

### Roles

- **Worker**: crea la rama del ticket desde `develop`, commitea, ejecuta los
  gates y marca `completado`.
- **Conductor**: revisa, verifica con `--verify`, marca `verificado` y mergea a
  `develop` en orden de dependencias.
- **Usuario**: confirma los tickets y ejecuta la release (`taiki release`) sobre
  `main`.

### Migración

Un repositorio sin `develop` sigue funcionando: `taiki worktree list` y
`taiki worktree merge` avisan (`no existe develop; se usa <rama actual>`) y
caen en la rama actual en vez de fallar. Para adoptar el flujo, desde un `main`
limpio y al día:

```bash
git switch main && git pull --ff-only
git switch -c develop && git push -u origin develop
```

Después:

1. Protege `develop` y `main` en el remoto: sin pushes directos, con revisión
   y CI obligatorias y **force-push prohibido en ambas** (en `main` tampoco
   para administradores).
2. Fija `orchestrator.integration_branch` a `develop` en `.taiki/config.json`
   (si falta, el valor por defecto ya es `develop`). Es el destino de los
   merges de worktrees y el origen de las ramas `agent/*` nuevas.
3. Rebasea los worktrees abiertos sobre la nueva rama de integración:
   `git switch agent/T-XXXX-<slug> && git rebase develop`, o retíralos y
   vuelve a crearlos con `taiki claim T-XXXX --worktree`.
4. Comprueba que `taiki worktree list` muestra
   `Rama de integración: develop` sin aviso.

`main` queda congelada hasta la primera release (`taiki release`).

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

