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
