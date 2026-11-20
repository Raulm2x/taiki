# Migración a v0.5.0

Notas para quien actualiza una instalación existente. El CHANGELOG se
regenera desde los commits en cada release, así que estas notas viven aquí.

## Esquema v9: migración automática

La DB se actualiza sola al abrir: no hay paso manual ni pérdida de tickets.
`taiki status` muestra el esquema resuelto tras el primer arranque.

- **`renamed_from`** (tabla nueva): registra los alias de códigos
  renumerados. Si un ticket pasó de `T-0014.02` a `T-0014.05`, el código viejo
  sigue resolviendo en búsquedas y enlaces, así que los `T-XXXX` de commits,
  docs y memoria antiguos no se rompen al reorganizar.
- **`completed_at`** se conserva al confirmar: antes se borraba y las métricas
  de cierre perdían la fecha real de finalización. Ahora la confirmación ya no
  destruye la evidencia de cuándo se completó cada ticket; los informes de
  cierre usan el valor real.

Si la DB tiene una versión **mayor** que el código, Taiki se niega a abrirla y
pide actualizar; nunca degrada el esquema.

## Superficie MCP: 34 → 39 herramientas

El catálogo de herramientas MCP crece de 34 a 39. Si tu cliente cachea el
catálogo, recárgalo (`taiki servers --reload` o reiniciando el cliente).

- **`gate_run`**: ejecuta los gates declarados de un ticket y devuelve el
  resultado por gate.
- **`unlink`** y **`links_repair`**: quitan y reparan enlaces colgantes
  (commits, archivos o URLs que ya no resuelven).
- **`epic_check`**: informa del estado derivado de una épica (tareas listas,
  en curso, bloqueadas) sin reclamarla.
- **`reject_delivery`**: rechaza una entrega con motivo obligatorio; reabre el
  ticket a `en_progreso` con `reopen=true`.
- **`heartbeat`** admite `action=abandon|steps_exhausted`: un worker puede
  liberar su lease de forma explícita al agotar pasos o abandonar, en vez de
  esperar a que caduque.
- **`transition`** acepta `evidence` estructurada (tests, commits, revisión)
  para marcar `verificado` con respaldo verificable.

El catálogo bajó a 32,4 KB recortando descripciones: menos tokens de contexto
por sesión sin perder superficie.

## CLI

- `taiki renumber --allow-root`: permite renumerar códigos planos en raíz,
  no solo tareas de una épica.
- `taiki perms`: muestra los permisos efectivos del guard y la allowlist de
  shell del proyecto.
- `taiki config show`: imprime la configuración resuelta (defaults incluidos).
- `taiki servers --reload`: recarga la instancia MCP tras un merge o una
  migración de esquema sin reiniciar OpenCode. Úsalo cuando las herramientas
  o envelopes cambien entre versiones.
- `taiki list --json-summary` incluye `iterations` y `attempts` por ticket:
  la misma información que el routing advice, en el resumen compacto.

## Guard

- Los mensajes de denegación ahora **sugieren alternativas permitidas**: si un
  comando está bloqueado, el mensaje indica qué comando permitido cubre la
  misma necesidad.
- Ningún deny destructivo se relajó: `sudo`, `rm -rf`, push, merge directo y
  escrituras fuera de scope siguen igual.
- `git merge-base` y otras **lecturas** de git ya se permiten: la allowlist
  cubría escrituras y bloqueaba de paso lecturas inofensivas.

## Release

- El nivel de versión ya no se degrada por un `Release-Bump: patch` aislado:
  un trailer puntual en un `feat:` pequeño no baja el nivel que el conjunto de
  la release merece. Los trailers siguen siendo la forma de declarar el bump.
