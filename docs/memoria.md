# Memoria

Volver a [README](../README.md) · [Índice de documentación](README.md)

Taiki puede destilar lo aprendido en un proyecto en **memorias pequeñas y
revisables**. El objetivo no es guardar conversaciones: es recuperar contexto
operativo al empezar una tarea sin arrastrar todo el historial.

- **CLI**: `taiki memory`, `taiki memory playbook`, `taiki remember`,
  `taiki brief`, `taiki memory candidates` y `taiki memory review`.
- **MCP**: `brief`, `memory_search`, `memory_write`, `memory_playbook`,
  `memory_checklist`, `memory_candidates`, los recursos `taiki://brief`,
  `taiki://playbook/{slug}` y `taiki://memory/candidates`, la plantilla
  `taiki://memory/{code}` y los prompts `taiki:brief`, `taiki:playbook` y
  `taiki:curate`.
- **Datos**: viven en la misma base SQLite del proyecto. No hay memoria global.

## Qué es y qué no es

Una memoria es un hecho, una preferencia, una convención, un pitfall, una
decisión, un flujo de trabajo o una referencia. Se escribe en una o dos frases
accionables y lleva evidencia cuando la propone un agente.

No es:

- una transcripción de la sesión;
- un sustituto del ticket, del commit o del historial;
- una instrucción automática: una propuesta no se aplica hasta que el usuario
  la aprueba;
- un lugar para credenciales, secretos ni narrativas largas.

## Capas

### Scope

| Scope | Uso | Presupuesto por defecto |
|---|---|---|
| `project` | Convenciones, decisiones y pitfalls del repositorio | 3000 caracteres |
| `user` | Preferencias del usuario que aplican a este proyecto | 1200 caracteres |

`taiki remember` crea una memoria de scope `user` y tipo `preference`. Las
memorias de proyecto se crean con `taiki memory add` o con `memory_write`.

### Tipos

| Tipo | Para qué |
|---|---|
| `fact` | Dato estable del proyecto («el CLI vive en `bin/taiki`»). |
| `preference` | Preferencia del usuario («commits en español»). |
| `convention` | Convención de código o proceso. |
| `pitfall` | Error recurrente y cómo evitarlo. |
| `decision` | Decisión tomada y su motivo. |
| `workflow` | Secuencia operativa que funciona. |
| `reference` | Enlace o ruta de consulta. |
| `playbook` | Procedimiento completo, exportable como skill. |

### Tipos de ticket (`ticket_kinds`)

`kind` y `ticket_kinds` son conceptos distintos y no se mezclan:

- `kind` es el **tipo de memoria** (`fact`, `pitfall`, `convention`…).
- `ticket_kinds` es una lista opcional de **tipos de ticket** a los que aplica
  la memoria: `mantenimiento`, `funcionalidad`, `bug`, `documentacion`,
  `seguridad` u `otro`.

Una memoria sin `ticket_kinds` es **general**: aplica a cualquier ticket, igual
que las memorias creadas antes de la migración v6. Una memoria etiquetada solo
aplica a los tipos que incluye. El checklist del reviewer filtra por el tipo del
ticket revisado y los hints descartan las memorias etiquetadas que no casan.

La columna `ticket_kinds` es un JSON en `memories`. La migración v6 es aditiva
(`ALTER TABLE`) y no reescribe memorias existentes: quedan con la lista vacía y
se comportan como generales.

```bash
taiki memory add --title "Riesgo en pagos" --content "..." \
  --kind pitfall --ticket-kinds bug funcionalidad --evidence T-0052
taiki memory list --ticket-kinds bug
taiki memory edit M-0001 --ticket-kinds seguridad
```

### Estados

| Estado | Significado |
|---|---|
| `proposed` | Propuesta pendiente de aprobación humana. No es instrucción. |
| `active` | Vigente y aplicable. |
| `stale` | Entrada válida que conviene revisar; el brief la muestra como aviso. En P1 no se genera automáticamente. |
| `archived` | Retirada; no aparece en el brief. El histórico queda en el ledger. |
| `superseded` | Sustituida por otra memoria; se conserva para trazabilidad. |

## Ciclo de vida

```text
propuesta ──approve──▶ activa ──rm──▶ archivada
    │                    │
    └──reject──▶ archivada└──nueva clave──▶ superseded
```

- **Aprobar** (`taiki memory approve M-0001`): `proposed → active`. Solo el
  usuario puede hacerlo; el CLI exige actor `usuario`.
- **Rechazar** (`taiki memory reject M-0001 --why "..."`): `proposed →
  archived`; el motivo queda en el ledger.
- **Archivar** (`taiki memory rm M-0001 --why "..."`): `active`, `proposed` o
  `stale → archived`. No borra la fila.
- **Editar** (`taiki memory edit M-0001 ...`): corrige título, contenido,
  descripción, clave, tipo, confianza o rutas.
- **Sustituir**: una nueva memoria con la misma clave reemplaza a la vigente y
  la anterior pasa a `superseded`. También ocurre al cambiar la clave con una
  operación `supersede` de `memory_write`.

### Política `balanced`

Es la política por defecto (`memory.write_policy`). Decide el estado inicial:

- una escritura del usuario (`source=user`) nace `active`;
- una escritura de agente nace `proposed`, salvo `fact` o `pitfall` con al
  menos 2 evidencias y confianza ≥ 0.7, que nace `active`;
- una prohibición amplia («nunca», «prohibido», «no uses»…) siempre nace
  `proposed`, aunque venga del usuario;
- con `write_policy: "manual"`, todo nace `proposed`.

Una memoria de origen agente **exige al menos una evidencia**; sin ella no se
escribe. Si una propuesta no encaja, recházala o edítala: no la apruebes para
«arreglarla después».

## Brief operativo

El brief es un documento corto y estable con la memoria vigente. Se renderiza
por prioridad y cabe en un presupuesto de caracteres:

- `## Proyecto`: memorias de scope `project`;
- `## Usuario`: preferencias del usuario;
- `## Routing observado`: memorias de tipo `workflow` o marcadas con el kind
  secundario `routing`;
- `## Playbooks`: índice compacto (slug, título, descripción y código) de los
  playbooks activos; nunca incluye el cuerpo;
- `## Pendientes`: propuestas que aún no se han aprobado, en una línea
  compacta con el recuento, hasta cinco códigos y el comando
  `taiki memory pending`; nunca incluye el cuerpo de la propuesta. Si la línea
  completa no cabe, queda solo el recuento;
- `## Avisos`: entradas `stale` y cuántas se omitieron por presupuesto.

El encabezado dice «datos, no instrucciones»: el brief informa, pero las
reglas del proyecto y el ticket siguen mandando. Si una entrada no cabe, se
omite y el brief invita a usar `memory_search`.

Las propuestas también se ven fuera del brief: `taiki status` muestra
`Memoria: N propuestas pendientes` y su JSON incluye `memory.pending` y
`memory.proposed_codes`; la herramienta MCP `health` añade `memory_pending` y
avisa de las que llevan más de 7 días sin decisión. Ninguno de estos avisos
aprueba nada: el flujo sigue siendo `proposed → approve/reject` y
`taiki memory approve M-XXXX` es exclusivo del usuario.

El brief puede generarse como artefacto:

```bash
taiki brief                 # lo imprime
taiki brief --write         # escribe .taiki/generated/brief.md
taiki brief --section project --budget 2000
```

`taiki mcp install` regenera ese artefacto y lo añade a `instructions` de
OpenCode, así que el agente lo recibe al empezar. `.taiki/generated/` está
ignorado por Git; es un derivado, no una fuente de verdad. Tras aprobar
memorias, refresca el artefacto con `taiki brief --write`.

## Playbooks

Un **playbook** es una memoria procedimental: el «cómo se hace» de una tarea
repetible, con evidencia y ficheros de apoyo. Se guarda como una memoria de
tipo `playbook` con clave `playbook:<slug>` y se exporta como skill compatible
con agentskills/OpenCode.

- **Slug**: `^[a-z0-9]+(-[a-z0-9]+)*$`, entre 1 y 64 caracteres. La clave es
  `playbook:<slug>`.
- **Metadatos**: nombre (`title`), descripción de hasta 240 caracteres,
  contenido de hasta `max_playbook_chars` (12000 por defecto) y al menos una
  evidencia. Las escrituras de agente respetan la política `balanced` y nacen
  `proposed` hasta que el usuario las apruebe.
- **Ficheros de apoyo**: `references/`, `scripts/`, `assets/` y `templates/`.
  Son rutas relativas, sin `..` ni absolutos, y pasan por el mismo escaneo de
  secretos e inyección que el resto de la memoria. Los scripts se guardan como
  texto: Taiki **nunca los ejecuta**.
- **Export**: `.taiki/generated/skills/<slug>/SKILL.md` con frontmatter
  `name`, `description` y `metadata.taiki` (`code` y `evidence`), más los
  apoyos en sus rutas. La escritura es atómica e idempotente; reexportar un
  playbook retira los apoyos que ya no declaró.
- **Limpieza**: `export --all` solo exporta playbooks `active` y retira del
  directorio de skills los de playbooks archivados o sustituidos; no toca
  directorios ajenos.
- **Sync con OpenCode**: `taiki mcp install` llama al export y añade
  `.taiki/generated/skills` a la lista `skills` de `opencode.json` (idempotente);
  `taiki mcp uninstall` retira esa entrada sin tocar el resto.
- **Carga progresiva**: el brief solo muestra el índice; el cuerpo y los apoyos
  se cargan bajo demanda con `memory_playbook(slug)` o el recurso
  `taiki://playbook/{slug}`. El revisor puede pedir un apoyo concreto con
  `path` en vez de descargar el skill completo.

```bash
taiki memory playbook add --slug revisar-pr \
  --title "Revisar PR" \
  --description "Checklist para revisar un pull request." \
  --content "1. Lee el diff. 2. Ejecuta los gates." \
  --support references/checklist.md \
  --evidence T-0047.05

taiki memory playbook list                 # activos y propuestos
taiki memory playbook list --all           # incluye archivados y sustituidos
taiki memory playbook show revisar-pr      # metadatos, cuerpo, apoyos y evidencia
taiki memory playbook export revisar-pr    # un skill
taiki memory playbook export --all         # todos los activos
taiki memory playbook archive revisar-pr --why "Ya no aplica"
```

Un playbook archivado deja de exportarse y sale del índice del brief; el
histórico sigue en el ledger. `memory playbook archive` nunca borra.

## Candidatos y revisión

El motor de candidatos es **determinista**: lee tickets, eventos, comentarios,
enlaces, rutas, intentos y asignaciones, y propone qué merece una revisión. No
usa un LLM, no hace red y **no escribe memoria**: solo prepara candidatos con
evidencia.

### Reglas

| Regla | Señal | Qué propone |
|---|---|---|
| `rework` | `iterations >= 2` o reapertura desde `completado`/`verificado` | Revisar la corrección que se repitió. |
| `rejection` | Transición a `en_progreso` con comentario `Rechazado en revisión: …` | Recordar el criterio que faltó. |
| `blocked` | Paso por `bloqueado` o ticket bloqueado | Anticipar la decisión o dependencia. |
| `expensive_close` | `iterations >= 3` | Atajar un flujo que cuesta demasiados intentos. |
| `routing` | Par `(kind, assigned_to)` con muestra suficiente | Observar qué perfil resuelve mejor ese tipo. |
| `hotspot` | Varios tickets con incidencias bajo el mismo prefijo de ruta | Revisar una zona del proyecto. |
| `directive` | Comentario explícito del usuario (`usa`, `prefiere`, `siempre`, `nunca`…) | Persistir una instrucción durable. |

El routing exige al menos **5 muestras** (`candidate_routing_min_samples`),
calcula la **cota inferior de Wilson** (mínimo 0.5) y solo propone cuando la
tasa de éxito supera la media en al menos 0.15. Los hotspots necesitan al menos
2 tickets con incidencias en el mismo prefijo. Una prohibición amplia del
usuario exige 2 evidencias; una directiva explícita no prohibitoria basta con
una.

Cada candidato se agrupa por `(regla, tipo sugerido, prefijo de ruta, motivo
normalizado)` y se deduplica. Incluye `rule`, `title`, `summary`, `evidence`
(ticket/commit), `sample_size`, `confidence`, `paths`, `suggested_key` y
`details`. El motor descarta fallos transitorios (timeout, flaky, reintentos de
infraestructura), secretos, narrativas de una sola tarea y prohibiciones
amplias sin evidencia suficiente.

### Umbrales

| Clave | Defecto | Uso |
|---|---:|---|
| `candidate_min_evidence` | 2 | Evidencia mínima para prohibiciones y hotspots. |
| `candidate_lookback_days` | 90 | Ventana de histórico que se analiza. |
| `candidate_routing_min_samples` | 5 | Muestras mínimas por par `(kind, perfil)`. |
| `candidate_max` | 20 | Máximo de candidatos por consulta. |
| `candidate_similarity` | 0.8 | Umbral de similitud validado (0 < x ≤ 1). |

### CLI

```bash
taiki memory candidates                 # lista acotada por candidate_max
taiki memory candidates --since 30d --kind pitfall --limit 5
taiki memory candidates --json          # contrato estable para agentes
taiki memory candidates --all           # ignora candidate_max

taiki memory review                     # paquete para el curator
taiki memory review --json
taiki memory review --write             # .taiki/generated/memory-review.json

taiki memory pending --diff             # propuestas con diff resumido
```

`--since` acepta una ventana en días (`90d`) o una fecha ISO. `--kind` filtra
por el tipo de memoria sugerida. `review --write` escribe el paquete de forma
atómica y `.taiki/generated/` está ignorado por Git. `pending --diff` compara
la propuesta con la memoria que sustituye y resume los campos cambiados.

### MCP y curator

La herramienta `memory_candidates` es de solo lectura:

- `mode=list` devuelve `{count, candidates}` con los candidatos compactos;
- `mode=review` devuelve el paquete completo (`generated_at`, `candidates`,
  `current_memories`, `recent_tickets`, `thresholds`, `rules` e
  `instructions`);
- acepta `since`, `kind` y `limit` (20 por defecto).

También están el recurso `taiki://memory/candidates` y el prompt
`taiki:curate`. El prompt guía al subagente **curator**: lee el paquete, propone
como máximo **5 memorias**, prefiere `patch`/`supersede` sobre `add`, exige
evidencia, no aprueba, no edita código, no lanza subagentes y no aprende de
fallos transitorios, secretos ni prohibiciones amplias.

El conductor lanza el curator en background **cada 5 tickets verificados o
cerrados**, o antes si `memory_candidates(mode=list)` acumula **3 o más
candidatos**. Después publica las propuestas pendientes; la aprobación sigue
siendo exclusiva del usuario. El curator solo propone y el usuario decide; P3
añade playbooks, routing advice y checklist, pero no curado automático ni
métricas de impacto.

## Routing y checklist

### Routing advice

`routing_advice(conn, kind, paths, config)` observa el histórico cerrado y
devuelve el perfil que mejor resolvió tareas de ese `kind`/rutas:

- exige al menos `candidate_routing_min_samples` (5 por defecto);
- calcula la **tasa de primer intento** (`iterations <= 1` y no cancelado), la
  **cota inferior de Wilson** y los **intentos medios**;
- devuelve `profile`, `variant`, `steps`, `sample`, `first_pass`,
  `confidence`, `avg_attempts` y `reason`, o `None` si no hay muestra;
- con `paths` solo usa tickets que tocan esas rutas; no extrapola de rutas
  ajenas.

El MCP incluye este consejo en `agents` (cuando se pasa `kind`) y en
`schedule` (una entrada por tarea lista) dentro de `advice`. Es una sugerencia
con muestra y confianza: **nunca cambia la configuración ni lanza agentes**.
El conductor decide si aplica el perfil, la variante o los steps.

### Checklist del reviewer

`memory_checklist(kind, memory_kind, paths, limit)` devuelve hasta 5 pitfalls y
convenciones `active` que casan con el cambio:

- `kind` es el **tipo de ticket** (`bug`, `funcionalidad`, `mantenimiento`…);
  filtra por la etiqueta `ticket_kinds` de la memoria. Las memorias sin etiqueta
  son generales y entran siempre.
- `memory_kind` es opcional y filtra por **tipo de memoria** (`pitfall` o
  `convention`).
- `paths` filtra por rutas y cada entrada es compacta, con evidencia y motivo.

El reviewer pide el checklist antes de revisar, completa con `memory_search` y
propone un `pitfall` si detecta un fallo recurrente, sin aprobarlo. Los hints de
`claim` aplican la misma compatibilidad: una memoria etiquetada para otro tipo
de ticket no se sugiere.

## Presupuesto

El presupuesto es un techo por scope, no un objetivo. Las entradas `active`,
`proposed` y `stale` consumen caracteres; las archivadas y sustituidas no.

| Clave | Defecto | Qué controla |
|---|---:|---|
| `project_budget_chars` | 3000 | Total del scope `project`. |
| `user_budget_chars` | 1200 | Total del scope `user`. |
| `brief_budget_chars` | 4500 | Tamaño del brief renderizado. |
| `max_entry_chars` | 600 | Título y contenido de una entrada. |
| `max_playbook_chars` | 12000 | Cuerpo de un playbook y cada fichero de apoyo. |
| `playbook_index_chars` | 400 | Índice de playbooks dentro del brief. |

Si una escritura deja el scope por encima del límite, falla con el total usado,
el límite y las entradas que ocupan sitio; no recorta en silencio. Para liberar
espacio, ejecuta `taiki memory curate`, archiva (`rm`) o edita entradas. El
curado solo recorta lo no protegido y lo deja en el ledger.

Los playbooks no consumen el presupuesto del scope: se cargan de forma
progresiva y solo su índice consume `playbook_index_chars`. El cuerpo y los
apoyos quedan bajo demanda.

Prioridad de selección para el brief: fijadas (`pin`) primero, luego scope
`user`, después confianza, recencia y número de usos.

## Curado determinista

`taiki memory curate` mantiene la memoria sana sin LLM y sin borrar filas:

- `active → stale` cuando la entrada lleva más de `stale_after_days` sin uso ni
  actualización.
- `stale → archived` cuando lleva más de `archive_after_days` en ese estado.
- `pin` y `lock` quedan **exentos**: el curado no los caduca, archiva ni recorta.
- **Dedupe**: agrupa por fingerprint exacto, por la misma clave o por similitud
  (`candidate_similarity`) dentro del scope. Gana la entrada de mayor prioridad
  (pin/lock, estado vigente y prioridad normal); el perdedor se archiva y sus
  evidencias y ficheros se absorben en el ganador.
- **Presupuesto**: si un scope supera su límite, archiva primero las entradas de
  menor prioridad no protegidas hasta cumplirlo y reporta las retiradas. Si no
  puede recortar porque todo está fijado, lo deja explícito en el informe.
- Cada transición y merge deja un asiento en el ledger. `--dry-run` calcula el
  mismo plan dentro de un savepoint y lo revierte: no cambia estados ni ledger.
  El curado nunca aprueba propuestas ni borra datos.

## Métricas e impacto

`taiki memory stats` devuelve conteos por estado, scope y kind, presupuesto por
scope, propuestas pendientes, uso (`by_action`, `use_count`, feedback y último
uso) y routing observado por `(tipo de ticket, perfil)`.

`taiki memory impact` resume cierres, intentos, lead time, verificaciones y
fallos de la ventana, agrupados por semana. Son **datos descriptivos**: una
tendencia no demuestra que la memoria haya causado la mejora. No se calculan
métricas por proveedor ni se extrapolan muestras pequeñas.

## Salud de memoria

La herramienta MCP `health` incluye `memory` con `enabled`, `db_ok`, `entries`,
`pending`, `stale`, `budget`, `generated_synced` e `issues[]`. Comprueba que las
tablas y el FTS de memoria existan y estén sincronizados, que los artefactos
generados (brief y skills activos) estén presentes, y avisa de propuestas con
más de 7 días, entradas `stale` y presupuesto excedido. Los avisos no bloquean
`health`; solo una integridad rota (`db_ok=false`) se considera grave. No
aprueba nada.

## Evidencia

Tipos admitidos:

| Tipo | Ejemplo |
|---|---|
| `ticket` | `T-0045.05` |
| `commit` | `3bea4b9` o el hash completo |
| `file` | `docs/memoria.md` |
| `url` | `https://…` |
| `session` | `ses_…` |

Una evidencia de ticket también rellena `ticket_id`. La evidencia no es
decorativa: permite revisar de dónde salió la lección y es obligatoria para
cualquier escritura de agente.

## Seguridad

Antes de guardar, Taiki escanea títulos, contenidos, evidencias y motivos:

- **Secretos**: claves OpenAI, tokens GitHub, claves AWS, claves privadas y
  credenciales en claro (`password: …`, `token=…`). Se rechaza la escritura y
  el mensaje no repite el valor.
- **Inyección de prompt**: frases como «ignora las instrucciones anteriores» o
  «system prompt». Se rechaza para que una memoria no se convierta en una
  orden encubierta.
- **Unicode invisible y caracteres de control**: se rechazan para evitar texto
  que se lea distinto de lo que parece.
- **Rutas de ficheros**: solo relativas y dentro de `references/`, `scripts/`,
  `assets/` o `templates/`; nada de absolutos, `..` ni enlaces que salgan del
  proyecto.

Además, toda propuesta pasa por aprobación humana. Un agente no puede aprobar
ni rechazar: el CLI exige actor `usuario`.

## Ledger, export/import y rollback

Cada alta, edición, aprobación, rechazo, archivado, fijado, feedback,
supersesión o curado deja un asiento **append-only** en `memory_ledger`, con
actor, sesión, acción, motivo y hashes del estado anterior y posterior. Los
snapshots completos se guardan como blobs content-addressed. `taiki memory show
M-0001` muestra los últimos asientos y el uso de la entrada.

**Exportación**. `taiki memory export <ruta>` escribe un snapshot con memorias,
playbooks, ficheros, evidencia, propuestas y relaciones. Incluye `format`,
`version` y un `checksum` sha256 determinista; los secretos conocidos se
redactan antes de escribir. En JSON es directamente reimportable. En Markdown
la parte visible es un informe y el JSON canónico viaja embebido para que la
importación no pierda datos.

**Importación**. `taiki memory import <ruta>` valida formato, versión, checksum
y contenido (secretos, rutas, límites) antes de escribir. Estrategias:

- `keep`: omite conflictos con el código o la clave existente.
- `replace`: actualiza la entrada existente con los datos del snapshot.
- `new`: inserta con un código nuevo.

`--dry-run` devuelve el informe de conflictos sin escribir. La importación deja
ledger y no borra filas.

**Rollback**. `taiki memory rollback <ledger-id>` restaura el estado anterior a
ese asiento y registra un asiento nuevo. `taiki memory rollback --snapshot
<fichero>` reimporta el snapshot en modo `replace` exacto: restaura las
entradas y archiva las que sobran, sin borrarlas. Ambos admiten `--dry-run` y
quedan auditados. Para retirar algo de circulación manualmente usa `taiki memory
rm` (archiva, nunca borra).

## Auto-curado y review_command (opt-in)

`memory.auto_curate` está desactivado por defecto. Con `enabled=true`, `taiki
status`, `taiki next` y `taiki memory curate` ejecutan el curado determinista
cuando toca según `interval_hours` y `idle_minutes`; `taiki memory curate` lo
fuerza explícitamente. La marca `last_run` vive en la meta de la DB y `--dry-run`
no la actualiza. El auto-curado no aprueba ni borra.

`memory.review_command` es opcional. Si se configura, `taiki memory review
--run-command` envía el paquete de revisión por stdin al comando y espera JSON
por stdout: una lista de operaciones o `{"operations": [...]}`. Taiki valida la
salida, aplica un timeout (`review_timeout_seconds`) y convierte las altas en
propuestas `proposed` para que las revise el usuario. En esta versión el comando
solo puede proponer `add`, hasta 5 entradas, y no puede reemplazar claves
existentes. El núcleo no usa API keys ni red; el comando externo es
responsabilidad de quien lo configura.

## CLI

```bash
# Preferencia del usuario (scope user, tipo preference)
taiki remember "Prefiero commits en español"
taiki remember "El gate exige ruff" --ticket T-0045.05

# Alta de proyecto
taiki memory add --scope project --kind convention \
  --title "Los docs van en docs/" \
  --content "La documentación vive en docs/ y se enlaza desde docs/README.md." \
  --evidence T-0045.05 --evidence 3bea4b9 --pin

# Playbooks (procedimiento exportable como skill)
taiki memory playbook add --slug revisar-pr \
  --title "Revisar PR" \
  --description "Checklist para revisar un pull request." \
  --content "1. Lee el diff. 2. Ejecuta los gates." \
  --support references/checklist.md \
  --evidence T-0047.05
taiki memory playbook list
taiki memory playbook show revisar-pr
taiki memory playbook export --all
taiki memory playbook archive revisar-pr --why "Ya no aplica"

# Consulta
taiki memory list
taiki memory list --kind pitfall --status active
taiki memory show M-0001

# Revisión
taiki memory pending
taiki memory pending --diff            # cambios frente a la memoria sustituida
taiki memory approve M-0002
taiki memory reject M-0003 --why "Es un fallo transitorio"

# Candidatos y paquete de revisión
taiki memory candidates
taiki memory candidates --since 30d --kind pitfall --limit 5 --json
taiki memory review
taiki memory review --write            # .taiki/generated/memory-review.json
taiki memory review --run-command      # review_command opt-in → propuestas
taiki memory review --run-command --dry-run

# Curado y métricas
taiki memory curate
taiki memory curate --dry-run
taiki memory curate --json
taiki memory stats
taiki memory stats --json
taiki memory impact --days 30
taiki memory impact --days 30 --json

# Export/import y rollback
taiki memory export .taiki/generated/memoria.json
taiki memory export .taiki/generated/memoria.md --format markdown
taiki memory import .taiki/generated/memoria.json --strategy keep --dry-run
taiki memory import .taiki/generated/memoria.json --strategy replace
taiki memory rollback 42 --dry-run
taiki memory rollback 42
taiki memory rollback --snapshot .taiki/generated/memoria.json

# Mantenimiento
taiki memory edit M-0001 --content "Texto corregido"
taiki memory pin M-0001
taiki memory unpin M-0001
taiki memory rm M-0001 --why "Ya no aplica"
```

`memory list` excluye archivadas y sustituidas salvo que pidas ese estado.
`--json` está disponible en todos los subcomandos. `memory rm` archiva: no hay
borrado destructivo. `memory curate` nunca aprueba ni borra; `memory impact`
describe tendencias y no afirma causalidad.

## MCP

| Herramienta | Uso |
|---|---|
| `brief` | Brief operativo; `section`, `budget` y `write`. |
| `memory_search` | Busca por texto y filtra por `scope`, `kind`, `status`, `paths`, `kinds` o `ticket_kinds`; devuelve snippets salvo `include_content=true`. |
| `memory_write` | Aplica operaciones `add`, `patch`, `remove`, `supersede` o `feedback` con evidencia y la política del workspace; `kind=playbook` acepta `description` y `files [{path, content}]`; `ticket_kinds` etiqueta por tipo de ticket; devuelve un recibo con presupuesto y avisos. |
| `memory_playbook` | Solo lectura: carga un playbook por `slug` (título, descripción, cuerpo, apoyos y evidencia); `path` carga un apoyo concreto. |
| `memory_checklist` | Solo lectura: pitfalls y convenciones activos por tipo de ticket (`kind`), tipo de memoria (`memory_kind`) y rutas; `limit` de 1 a 20. |
| `memory_candidates` | Solo lectura: `mode=list` lista candidatos; `mode=review` devuelve el paquete del curator. Acepta `since`, `kind` y `limit`. |
| `memory_stats` | Solo lectura: `mode=stats` (conteos, presupuesto, propuestas, uso y routing), `mode=impact` (tendencias) o `mode=all`; `days` ajusta la ventana de impacto. |
| `health` | Incluye la sección `memory` con integridad, cola, stale, presupuesto y artefactos generados; los avisos no bloquean salvo `db_ok=false`. |

Recursos: `taiki://brief` (Markdown), `taiki://memory/{code}` (JSON),
`taiki://playbook/{slug}` (JSON) y `taiki://memory/candidates` (JSON).
Prompts: `taiki:brief`, que aplica el brief al empezar el trabajo;
`taiki:playbook`, que carga un procedimiento y recuerda revisar sus pitfalls; y
`taiki:curate`, que guía la revisión de candidatos sin aprobar ni editar.

El recibo de `claim` puede incluir `hints`: memorias activas relevantes por
rutas, tipo de ticket o etiquetas, más hasta dos playbooks relevantes con
`slug`, `title`, `description` y `reason` para cargarlos con `memory_playbook`.
Las memorias sin `ticket_kinds` son generales; las etiquetadas para otro tipo de
ticket se omiten. El recibo de `complete` puede incluir `learning`: una
sugerencia para proponer una lección tras rework o reapertura. Son ayudas, no
órdenes.

## Configuración

El bloque `memory` vive en `.taiki/config.json`. Las claves desconocidas fallan
al arrancar para que una errata no se ignore en silencio.

```json
{
  "memory": {
    "enabled": true,
    "write_policy": "balanced",
    "project_budget_chars": 3000,
    "user_budget_chars": 1200,
    "brief_budget_chars": 4500,
    "max_entry_chars": 600,
    "max_playbook_chars": 12000,
    "playbook_index_chars": 400,
    "stale_after_days": 45,
    "archive_after_days": 120,
    "candidate_min_evidence": 2,
    "candidate_lookback_days": 90,
    "candidate_routing_min_samples": 5,
    "candidate_max": 20,
    "candidate_similarity": 0.8,
    "instructions_sync": true,
    "skills_sync": true,
    "user_scope": "project",
    "auto_curate": {
      "enabled": false,
      "interval_hours": 24,
      "idle_minutes": 0
    },
    "review_command": null,
    "review_timeout_seconds": 15
  }
}
```

- `enabled`: apaga la memoria sin borrar datos.
- `write_policy`: `balanced` o `manual`.
- `user_scope`: scope por defecto de las preferencias de usuario.
- `stale_after_days` y `archive_after_days`: umbrales validados de caducidad;
  deben cumplir `archive_after_days >= stale_after_days`. Los aplica `taiki
  memory curate`; `0` es un umbral inmediato, no un interruptor. El auto-curado
  tiene su propio `enabled`.
- `max_playbook_chars`: límite del cuerpo de un playbook y de cada fichero de
  apoyo; los playbooks no consumen el presupuesto del scope.
- `playbook_index_chars`: tamaño máximo del índice de playbooks en el brief;
  si no cabe, la sección se omite o se recorta por entradas completas.
- `candidate_*`: controlan el motor de candidatos (evidencia mínima, ventana,
  muestras de routing, máximo y similitud); se validan al arrancar y se aplican
  en `memory candidates` y `memory_candidates`.
- `instructions_sync` y `skills_sync` se validan como parte del bloque;
  `skills_sync=false` evita exportar playbooks al sincronizar OpenCode.
- `auto_curate`: `enabled` (por defecto `false`), `interval_hours` y
  `idle_minutes`. Con el opt-in activo, `status`, `next` y `memory curate`
  lanzan el curado cuando corresponde; `memory curate` lo fuerza.
- `review_command`: comando externo opcional para `memory review --run-command`.
  Recibe el paquete por stdin y devuelve operaciones JSON; el núcleo no usa API
  keys ni red.
- `review_timeout_seconds`: timeout del comando externo; por defecto 15 s.

## Flujo recomendado

1. Al empezar, aplica el brief y busca solo el contexto que falte con
   `memory_search`.
2. Si el ticket encaja con un playbook del índice, cárgalo con
   `memory_playbook(slug)` y sus apoyos solo cuando los necesites.
3. Trabaja con el ticket como fuente de verdad.
4. Si hubo corrección, rework o un workaround no obvio, propón una lección con
   evidencia. No guardes fallos transitorios, credenciales, narrativas ni
   prohibiciones amplias.
5. Antes de revisar, pide `memory_checklist(kind="pitfall"|"convention",
   paths=[...])` y completa con `memory_search` de pitfalls.
6. Cada 5 tickets verificados/cerrados o con 3 o más candidatos, revisa
   `taiki memory candidates` y lanza el curator con
   `taiki memory review --write` (o `memory_candidates(mode=review)` por MCP).
   El curator propone como máximo 5 memorias; no aprueba ni edita código.
7. El usuario revisa con `taiki memory pending --diff` y aprueba o rechaza.
8. Refresca el brief con `taiki brief --write` si quieres actualizar el
   artefacto inyectado.
9. Mantén la memoria con `taiki memory curate` (o el auto-curado opt-in) y
   revisa `taiki memory stats`/`impact`. Para versionar o mover memoria usa
   `memory export`/`import`; para deshacer un cambio, `memory rollback` por
   asiento o snapshot.

## FAQ

**¿Por qué no guarda transcripciones?**
Porque el objetivo es contexto reutilizable, no archivo de conversación. Una
transcripción es larga, contiene ruido y secreto accidental, y envejece mal.
La memoria destila una regla o un dato; el historial completo ya vive en el
ticket y en la sesión.

**¿Cómo evito que aprenda algo malo?**
Las escrituras de agente nacen `proposed`, exigen evidencia y pasan por
aprobación humana. El escaneo bloquea secretos e inyecciones. Si algo se cuela,
rechaza la propuesta, edítala o archívala con `rm`; el ledger conserva el
rastro.

**¿Cómo lo borro o lo edito?**
`taiki memory edit M-0001 ...` corrige una entrada. `taiki memory rm M-0001
--why "..."` la archiva (no borra la fila). Si el contenido cambia de fondo, crea
una entrada nueva con la misma clave o usa `supersede`; la anterior queda
`superseded` y fuera del brief.

**¿Quién aprueba?**
El usuario, con `taiki memory approve`. Los agentes pueden proponer, editar,
archivar o dar feedback, pero no aprobar ni rechazar.

**¿Cómo sé qué merece una lección?**
`taiki memory candidates` o `memory_candidates(mode=list)` agrupan rework,
rechazos, bloqueos, cierres costosos, routing, hotspots y directivas con
evidencia. No es una verdad automática: el curator propone y el usuario decide.
El motor no escribe memoria ni aprueba nada.

**¿La memoria sustituye al ticket?**
No. El ticket guarda la decisión, el estado, la evidencia y el historial. La
memoria solo destila lo que será útil en la siguiente tarea.

**¿Cómo actualizo el brief inyectado?**
`taiki brief --write` regenera `.taiki/generated/brief.md`; `taiki mcp install`
también lo regenera y mantiene la entrada en `instructions`.
