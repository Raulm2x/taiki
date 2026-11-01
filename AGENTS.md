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

## Flujo de trabajo

1. Consulta el trabajo pendiente: `taiki next`, `taiki list`, `taiki stats`.
2. Antes de tocar código, crea o reclama un ticket:
   `taiki new -t "..." -k mantenimiento -p alta` y `taiki claim T-0001 --agent opencode`.
3. Al terminar: `taiki complete T-0001 -r "qué se hizo y cómo se verificó" --commit <hash>`.
4. La confirmación es del usuario: `taiki confirm T-0001`. Nunca la ejecutes tú.
   Quien revisa puede marcar `verificado` antes con evidencia
   (`taiki transition T-0001 verificado --note "tests/CI/revisión"`).
5. El asunto del commit debe incluir `T-XXXX`; el hook `commit-msg` lo exige.
6. El conductor y los subagentes viven en `.opencode/agents/`; consulta el
   routing de variantes y el presupuesto de concurrencia con la herramienta
   MCP `agents` y `schedule` (paralelismo seguro por paths). `taiki status`
   muestra la cola (listos, en curso, verificando, bloqueados) y métricas.

## Agentes en paralelo

Varias sesiones pueden trabajar sobre el mismo proyecto. Para no pisarse:

- **Reclama antes de escribir** y mantén **un solo ticket activo por sesión**.
  Si otra sesión ya tiene el ticket, no lo reclames a la fuerza.
- **Un worktree por escritor.** Cada tarea que modifique código debe vivir en su
  propio `git worktree` (`.worktrees/<T-XXXX>`, ignorado por Git) con la DB
  compartida de `.taiki/`. El worktree puede contener el `.taiki/config.json`
  versionado, pero `taiki` resuelve siempre la DB del checkout principal; no
  crees una DB local dentro de `.worktrees/`. Las lecturas sí pueden compartir
  el árbol. Si otra sesión está activa, el guard **bloquea escribir en el
  checkout** sin worktree; el escape consciente es `TAIKI_SHARED_TREE=1`.
- **Al reclamar**: `taiki claim T-XXXX --worktree` crea el worktree y lo enlaza.
  Durante comandos largos, `taiki heartbeat T-XXXX` renueva el lease.
- **Al terminar**: commitea en el worktree, marca el ticket `completado` y
  espera la confirmación del usuario. Solo después, `taiki worktree merge
  T-XXXX --delete` hace rebase, tests, `merge --ff-only` y limpia. El merge
  rechaza tickets que no estén en `confirmado`; `--force` es el bypass
  consciente. `taiki confirm` retira solo los worktrees limpios y mergeados
  (`--keep-worktrees` para conservarlos).
- **Stagea rutas concretas** (`git add <ruta>`). Si otra sesión tiene un ticket
  activo, el guard bloquea `git add -A`, `git add .`, `git commit -a`,
  `git stash`, `git reset`, `git clean` y `git checkout -- .`.
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
