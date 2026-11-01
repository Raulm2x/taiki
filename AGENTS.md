# Taiki — guía para agentes

Taiki es un gestor local de tickets en SQLite. Este repositorio es el proyecto
completo y su base de datos vive en `.taiki/taiki.db` (ignorada por Git).

## Contexto rápido

- CLI: `taiki` (wrapper `bin/taiki`).
- MCP: `bin/taiki-mcp`, instalado **solo en este proyecto** con
  `taiki mcp install`. No hay MCP global.
- DB: `.taiki/taiki.db`. `config.json` y `.taiki/.gitignore` se versionan;
  `taiki.db`, `local.json` y `backups/` no.
- Guard: activo porque `.taiki/config.json` tiene `"guard": true`.

## Flujo de trabajo

1. Consulta el trabajo pendiente: `taiki next`, `taiki list`, `taiki stats`.
2. Antes de tocar código, crea o reclama un ticket:
   `taiki new -t "..." -k mantenimiento -p alta` y `taiki claim T-0001 --agent opencode`.
3. Al terminar: `taiki complete T-0001 -r "qué se hizo y cómo se verificó" --commit <hash>`.
4. La confirmación es del usuario: `taiki confirm T-0001`. Nunca la ejecutes tú.
5. El asunto del commit debe incluir `T-XXXX`; el hook `commit-msg` lo exige.

## Agentes en paralelo

Varias sesiones pueden trabajar sobre el mismo proyecto. Para no pisarse:

- **Reclama antes de escribir** y mantén **un solo ticket activo por sesión**.
  Si otra sesión ya tiene el ticket, no lo reclames a la fuerza.
- **Un worktree por escritor.** Cada tarea que modifique código debe vivir en su
  propio `git worktree` (`.worktrees/<T-XXXX>`, ignorado por Git) con la DB
  compartida de `.taiki/`. Las lecturas sí pueden compartir el árbol.
- **Stagea rutas concretas** (`git add <ruta>`). Si otra sesión tiene un ticket
  activo, el guard bloquea `git add -A`, `git add .`, `git commit -a`,
  `git stash`, `git reset`, `git clean` y `git checkout -- .`.
- **Nunca uses `git stash` a pelo**: el stash es único para todos los worktrees
  y puede aplicarte cambios de otra sesión. Commitea el WIP en tu rama.
- **Commitea solo lo tuyo** con `taiki commit -m "..."`: respeta las rutas del
  ticket (`taiki new --paths src/`) y rechaza el índice que solapa con otro
  ticket activo. Fusiona de una rama a la vez: rebase sobre la rama principal,
  tests de nuevo y `merge --ff-only`.
- Si el guard bloquea y no hay conflicto real, el bypass manual es
  `TAIKI_GUARD=off`.

## Comandos útiles

```bash
taiki init                  # DB y workspace actuales
taiki stats                 # resumen
taiki tui                   # dashboard del proyecto
taiki mcp install           # MCP de OpenCode solo para este proyecto
taiki mcp uninstall         # lo retira y desactiva el guard
taiki servers               # instancias MCP vivas (registro global)
python3 tests/run.py         # suite completa
python3 tests/run.py --audit # ruido, presupuesto y línea base
```

## Aislamiento

- No uses la DB de `~/.config/.taiki`: es el workspace del proyecto de dotfiles.
- `TAIKI_DB` o `--db` permiten apuntar a otra DB explícitamente.
- Cada proyecto arranca su propia instancia MCP; el registro de procesos en
  `~/.local/share/taiki/registry.db` es solo inventario, no contiene tickets.
