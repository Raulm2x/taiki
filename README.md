# Taiki

Taiki es un gestor local de tickets en SQLite para que los agentes de IA
registren y coordinen trabajo (mantenimiento, funcionalidades, bugs,
documentación, seguridad) y para que el usuario revise y confirme lo hecho.

- **CLI**: `taiki` para uso humano.
- **Servidor MCP**: `taiki` (stdio). No hay servidor global: se instala por
  proyecto con `taiki mcp install`.
- **Base de datos**: una por proyecto en `.taiki/taiki.db`. Sin base de datos
  global: usar Taiki es una decisión de cada proyecto.
- **Requisitos**: Python 3.11+ y su biblioteca estándar. Sin dependencias
  externas.

## Quick start

```bash
# CLI y servidor MCP en el PATH
ln -sfn ~/Projects/taiki/bin/taiki ~/.local/bin/taiki
ln -sfn ~/Projects/taiki/bin/taiki-mcp ~/.local/bin/taiki-mcp

# Activar Taiki en un proyecto
cd ~/Projects/mi-proyecto
taiki init --workspace --mcp   # crea .taiki/ y registra el MCP del proyecto

# Ciclo básico
taiki new -t "Revisar backups" -k mantenimiento -p alta
taiki list                     # abiertos, del más reciente al más antiguo
taiki claim T-0001 --worktree  # reclama y aísla en .worktrees/T-0001
taiki commit -m "feat: revisar backups (T-0001)"
taiki complete T-0001 -r "Qué se hizo y cómo se verificó" --commit abc1234
taiki confirm T-0001           # solo el usuario
taiki tui                      # dashboard interactivo
```

Comandos imprescindibles: `list`, `show`, `new`, `claim`, `complete`,
`confirm`, `status`, `next` y `tui`. `--json`, `--compact` y `--json-summary`
están pensados para agentes.

## Memoria operativa

`taiki` puede destilar lo aprendido en memorias pequeñas y revisables, sin
guardar transcripciones. El **brief** recupera el contexto vigente al empezar;
las lecciones de los agentes nacen como propuestas y solo el usuario las
aprueba.

```bash
taiki brief                    # brief operativo (datos, no instrucciones)
taiki remember "Prefiero commits en español"
taiki memory candidates        # qué merece revisión (rework, rechazos, hotspots…)
taiki memory review --write    # paquete para el subagente curator
taiki memory pending           # propuestas de los agentes
taiki memory pending --diff    # cambio frente a la memoria sustituida
taiki memory approve M-0001    # solo el usuario
```

Las memorias llevan evidencia (ticket, commit, archivo…), respetan un
presupuesto de caracteres y se archivan en vez de borrarse. El motor de
candidatos es determinista: no usa LLM ni escribe memoria. El subagente
**curator** lee el paquete de revisión y propone como máximo 5 memorias; el
conductor lo lanza cada 5 tickets cerrados o con 3+ candidatos. Los agentes
proponen y **solo el usuario aprueba**. Detalle: [Memoria](docs/memoria.md).

## Estados

```
nuevo → pendiente → en_progreso → completado → verificado → confirmado
                 ↘ bloqueado ↗                    ↘ en_progreso (rechazo)
                 ↘ cancelado ↗ (reabrible)
```

- `completado`: el worker terminó (commit, tests y criterios de aceptación).
- `verificado`: el conductor revisó y ejecutó los gates con evidencia.
- `confirmado`: solo lo aplica el usuario desde el CLI; después el conductor
  mergea en orden con `taiki worktree merge`.
- Reabrir un ticket exige un motivo e incrementa el contador de intentos.
- Cada cambio queda en el historial con actor, sesión y nota.

## Workspace mínimo

```text
mi-proyecto/
└── .taiki/
    ├── taiki.db          # SQLite del proyecto (ignorada por Git)
    ├── config.json       # configuración versionable
    ├── local.json        # ajustes de esta máquina (ignorado)
    └── .gitignore
```

El guard (`"guard": true` en `config.json`) bloquea las escrituras sin ticket
activo. `taiki mcp install` lo activa y `taiki mcp uninstall` lo desactiva.
Los agentes permiten shell de inspección, piden permiso para lo desconocido y
deniegan push/merge/confirm: [permisos](docs/operacion.md#permisos-de-shell).
El borrado requiere OK del conductor y una ruta exacta:
[borrado seguro](docs/operacion.md#borrado-seguro).

## Documentación

- [Instalación](docs/instalacion.md) — requisitos, wrappers, activación por
  proyecto y migración desde `ticket`.
- [Uso](docs/uso.md) — CLI diario, épicas y numeración, TUI, revisión y
  confirmación.
- [Memoria](docs/memoria.md) — brief, propuestas, aprobación, presupuesto,
  evidencia y seguridad.
- [MCP](docs/mcp.md) — herramientas, envelopes, recursos, prompts y sesiones.
- [Operación](docs/operacion.md) — workspaces, orquestación, guard, worktrees,
  tickets temporales, servidores MCP, datos y aislamiento.
- [Desarrollo](docs/desarrollo.md) — estructura, tests, gates, presupuesto y
  cobertura.
- [Ramas y releases](docs/desarrollo.md#ramas-y-releases) — `develop` para la
  integración diaria y `main` solo con releases etiquetadas.

Índice completo: [docs/README.md](docs/README.md).

## Licencia

MIT. Consulta [LICENSE](LICENSE).
