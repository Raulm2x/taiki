# Instalación

[← README](../README.md) · [Índice de documentación](README.md)

No tiene dependencias externas: solo Python 3.11+ y su biblioteca estándar. Los
wrappers resuelven su propia ruta con `readlink -f`, así que funcionan tanto
desde el repo como a través de un enlace simbólico.

```bash
ln -sfn ~/Projects/taiki/bin/taiki ~/.local/bin/taiki
ln -sfn ~/Projects/taiki/bin/taiki-mcp ~/.local/bin/taiki-mcp
```

Los wrappers `bin/ticket` y `bin/tickets-mcp` se retiraron cuando el comando
`taiki` quedó asentado. Si todavía tienes hooks antiguos en otro repositorio,
migralos antes de actualizar:

```bash
cd /ruta/al/repo && taiki hook-install
```

## Migración desde `ticket`

Taiki se llamaba `ticket` hasta esta versión. El comando y los wrappers
antiguos ya no existen; la compatibilidad de **datos** se mantiene:

| Antes | Ahora | Notas |
| --- | --- | --- |
| `ticket <cmd>` | `taiki <cmd>` | el alias se retiró; usa `taiki` |
| MCP `tickets` (tools `tickets_*`) | MCP `taiki` (tools `taiki_*`) | `tickets_*` y los recursos `tickets://` siguen aceptándose |
| `TICKETS_DB`, `TICKETS_GUARD`, `TICKETS_GUARD_AUTO`, `TICKETS_HOOK`, `TICKETS_AGENT` | `TAIKI_*` | las antiguas se leen como alias; las nuevas tienen prioridad |
| Hooks `# >>> tickets ... >>>` | `# >>> taiki ... >>>` | `taiki hook-install` reescribe los bloques sin duplicarlos |

`taiki mcp install` retira la entrada histórica `tickets` de `opencode.json` y
registra `taiki`. Después de actualizar, reinicia OpenCode para que recargue el
servidor MCP con el nombre nuevo.

## Activar Taiki en un proyecto

```bash
cd ~/Projects/mi-proyecto
taiki init --workspace          # crea .taiki/ con su DB y config.json
taiki mcp install               # registra el MCP solo en este proyecto
taiki mcp install --antigravity # opcional: Antigravity solo tiene alcance global
```

`taiki mcp install` escribe la configuración del cliente en la raíz del
proyecto (`opencode.json`) y activa `guard` en `.taiki/config.json`. En OpenCode
cada proyecto arranca su propia instancia MCP con su propia DB; no hay un
servidor global siempre activo.

Para quitarlo:

```bash
taiki mcp uninstall             # retira el MCP y desactiva el guard
```

Atajo equivalente:

```bash
taiki init --workspace --mcp
```

## Antigravity

El CLI de Antigravity no ofrece alcance por proyecto: `agy mcp add` escribe en
`~/.gemini/config/mcp_config.json` para todos los proyectos. Taiki no lo toca
por defecto; si lo quieres ahí, instálalo explícitamente:

```bash
taiki mcp install --antigravity
```

## Otros clientes MCP

```json
{
  "mcpServers": {
    "taiki": {
      "command": "python3",
      "args": ["/home/rowlm/Projects/taiki/server.py"]
    }
  }
}
```

El cliente debe arrancar el proceso con el directorio del proyecto como `cwd`
(es lo habitual). El servidor resuelve `.taiki/` desde ahí.
