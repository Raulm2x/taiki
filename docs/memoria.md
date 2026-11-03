# Memoria

Volver a [README](../README.md) · [Índice de documentación](README.md)

Taiki destila lo aprendido en un proyecto en **memorias pequeñas y revisables**:
contexto operativo reutilizable, no un archivo de conversaciones.

- **CLI**: `taiki memory`, `taiki memory playbook`, `taiki remember`,
  `taiki brief`, `taiki memory candidates` y `taiki memory review`. Comandos en
  [Uso › Memoria y brief](uso.md#memoria-y-brief).
- **MCP**: `brief`, `memory_search`, `memory_write`, `memory_playbook`,
  `memory_checklist`, `memory_candidates` y `memory_stats`; recursos
  `taiki://brief`, `taiki://playbook/{slug}` y `taiki://memory/candidates`;
  plantilla `taiki://memory/{code}`; prompts `taiki:brief`, `taiki:playbook` y
  `taiki:curate`. Contrato en [MCP](mcp.md#herramientas-mcp).
- **Datos**: la misma base SQLite del proyecto. No hay memoria global.

## Qué es y qué no es

Una memoria es un hecho, preferencia, convención, pitfall, decisión, flujo o
referencia, en una o dos frases accionables y con evidencia cuando la propone un
agente. **No es** una transcripción, un sustituto del ticket, del commit o del
historial, una instrucción automática (una propuesta no aplica hasta que el
usuario la aprueba) ni un lugar para credenciales, secretos o narrativas largas.
El ticket sigue siendo la fuente de verdad.

## Capas

### Scope

| Scope | Uso | Presupuesto por defecto |
|---|---|---|
| `project` | Convenciones, decisiones y pitfalls del repositorio | 3000 caracteres |
| `user` | Preferencias del usuario que aplican a este proyecto | 1200 caracteres |

`taiki remember` crea una memoria de scope `user` y tipo `preference`. Las de
proyecto se crean con `taiki memory add` o `memory_write`.

### Tipos

`fact` (dato estable como «el CLI vive en `bin/taiki`»), `preference`
(preferencia del usuario), `convention` (convención de código o proceso),
`pitfall` (error recurrente y cómo evitarlo), `decision` (decisión y motivo),
`workflow` (secuencia operativa que funciona), `reference` (enlace o ruta de
consulta) y `playbook` (procedimiento completo, exportable como skill).

### Tipos de ticket (`ticket_kinds`)

`kind` es el **tipo de memoria** (`fact`, `pitfall`, `convention`…).
`ticket_kinds` es una lista opcional de **tipos de ticket** a los que aplica:
`mantenimiento`, `funcionalidad`, `bug`, `documentacion`, `seguridad` u `otro`.
Son conceptos distintos y no se mezclan.

Sin `ticket_kinds` la memoria es **general**: aplica a cualquier ticket, igual
que las creadas antes de la migración v6. Etiquetada, solo aplica a los tipos
incluidos; el checklist filtra por el tipo del ticket revisado y los hints
descartan las que no casan. La columna es un JSON en `memories`; la migración v6
es aditiva (`ALTER TABLE`) y no reescribe filas: quedan vacías y generales.

```bash
taiki memory add --title "Riesgo en pagos" --content "..." \
  --kind pitfall --ticket-kinds bug funcionalidad --evidence T-0052
taiki memory list --ticket-kinds bug
taiki memory edit M-0001 --ticket-kinds seguridad
```

### Estados

| Estado | Significado |
|---|---|
| `proposed` | Pendiente de aprobación humana. No es instrucción. |
| `active` | Vigente y aplicable. |
| `stale` | Válida pero conviene revisarla; el brief la avisa. En P1 no se genera automáticamente. |
| `archived` | Retirada; no aparece en el brief. Su histórico queda en el ledger. |
| `superseded` | Sustituida por otra; se conserva para trazabilidad. |

## Ciclo de vida

| Acción | Comando | Efecto |
|---|---|---|
| Aprobar | `taiki memory approve M-0001` | `proposed → active`. Solo el usuario; el CLI exige actor `usuario`. |
| Rechazar | `taiki memory reject M-0001 --why "..."` | `proposed → archived`; el motivo queda en el ledger. |
| Archivar | `taiki memory rm M-0001 --why "..."` | `active`, `proposed` o `stale → archived`. No borra la fila. |
| Editar | `taiki memory edit M-0001 ...` | Corrige título, contenido, descripción, clave, tipo, confianza o rutas. |
| Sustituir | Nueva memoria con la misma clave | La vigente pasa a `superseded`; también con `supersede` de `memory_write`. |

### Política `balanced`

Es la política por defecto (`memory.write_policy`) y decide el estado inicial:

- escritura del usuario (`source=user`): nace `active`;
- escritura de agente: nace `proposed`, salvo `fact`, `convention` o `pitfall`
  con al menos una **evidencia validada** (ticket `verificado`/`confirmado`,
  commit resoluble o fichero existente) y `memory.auto_approve_evidence` en
  `true`, que nace `active`; también activa `fact` o `pitfall` con al menos 2
  evidencias y confianza ≥ 0.7;
- prohibición amplia («nunca», «prohibido», «no uses»…): siempre `proposed`,
  aunque venga del usuario o tenga evidencia validada;
- `workflow` y las memorias de routing siguen `proposed` salvo fuente de
  usuario/derivada/importada;
- con `write_policy: "manual"`, todo nace `proposed`.

`url` y `session` no se validan offline, así que no cuentan como evidencia
validada. `auto_approve_evidence=false` devuelve las altas de agente a
`proposed` salvo la regla antigua de 2 evidencias + confianza.

Una memoria de origen agente **exige al menos una evidencia**. Si una propuesta
no encaja, recházala o edítala: no la apruebes para «arreglarla después».

## Brief operativo

Documento corto y estable con la memoria vigente, renderizado por prioridad
dentro de un presupuesto de caracteres:

| Sección | Contenido |
|---|---|
| `## Proyecto` | Memorias de scope `project`. |
| `## Usuario` | Preferencias del usuario. |
| `## Routing observado` | Memorias `workflow` o con el kind secundario `routing`. |
| `## Playbooks` | Índice compacto (slug, título, descripción, código); nunca el cuerpo. |
| `## Pendientes` | Propuestas sin aprobar: recuento, hasta cinco códigos y `taiki memory pending`; nunca su cuerpo. Si no cabe, solo el recuento. |
| `## Avisos` | Entradas `stale` y cuántas se omitieron por presupuesto. |

El encabezado dice «datos, no instrucciones»: las reglas del proyecto y el
ticket mandan. Si una entrada no cabe, se omite y el brief invita a usar
`memory_search`.

Fuera del brief, `taiki status` muestra `Memoria: N propuestas pendientes` (JSON:
`memory.pending` y `memory.proposed_codes`) y `health` añade `memory_pending`,
con aviso a los 7 días sin decisión. Nada se aprueba solo:
`taiki memory approve M-XXXX` es del usuario.

```bash
taiki brief                 # lo imprime
taiki brief --write         # escribe .taiki/generated/brief.md
taiki brief --section project --budget 2000
```

`taiki mcp install` regenera el artefacto y lo añade a `instructions` de
OpenCode. `.taiki/generated/` está ignorado por Git: es un derivado, no una
fuente de verdad.

## Playbooks

Un **playbook** es una memoria procedimental (`kind=playbook`, clave
`playbook:<slug>`): un procedimiento repetible con evidencia y apoyos,
exportable como skill compatible con agentskills/OpenCode.

- **Slug** `^[a-z0-9]+(-[a-z0-9]+)*$`, 1–64 caracteres.
- **Metadatos**: `title`, descripción ≤240 caracteres, cuerpo ≤
  `max_playbook_chars` (12000 por defecto) y ≥1 evidencia. Las escrituras de
  agente respetan `balanced` y nacen `proposed`.
- **Apoyos**: `references/`, `scripts/`, `assets/` y `templates/`; rutas
  relativas sin `..` ni absolutos, con el mismo escaneo de secretos e inyección.
  Los scripts se guardan como texto: Taiki **nunca los ejecuta**.
- **Export** a `.taiki/generated/skills/<slug>/SKILL.md` con frontmatter `name`,
  `description` y `metadata.taiki` (`code`, `evidence`), más los apoyos en sus
  rutas. Escritura atómica e idempotente: reexportar retira los apoyos que ya no
  declaró. `export --all` solo exporta `active` y retira los skills de playbooks
  archivados o sustituidos sin tocar directorios ajenos.
- **Sync**: `taiki mcp install` exporta y añade `.taiki/generated/skills` a
  `skills` en `opencode.json` (idempotente); `uninstall` retira esa entrada sin
  tocar el resto.
- **Carga progresiva**: el brief solo indexa; cuerpo y apoyos se cargan con
  `memory_playbook(slug)` o `taiki://playbook/{slug}`. El revisor puede pedir un
  apoyo con `path` sin descargar el skill completo.
- `archive` exige `--why` y nunca borra; deja de exportarse y sale del índice.

Comandos y flags (`add`, `list`, `show`, `export`, `archive`) en
[Uso › Memoria y brief](uso.md#memoria-y-brief).

## Skills curadas de AutoSkills

Taiki puede proponer e instalar skills del catálogo de
[AutoSkills](https://www.autoskills.sh/) (repositorio `midudev/autoskills`) que
encajen con el stack real: fuente opcional que **no ejecuta código ajeno, no
necesita Node y nunca activa una skill sin aprobación**.

### Detección de stack

Local, sin red ni procesos externos. Lee `pyproject.toml`, `requirements*.txt`,
`setup.py`, `setup.cfg`, `Pipfile`, `manage.py`, `package.json`
(`dependencies`, `devDependencies`, `peerDependencies`), lockfiles
(`package-lock.json`, `yarn.lock`, `pnpm-lock.yaml`, `bun.lock`, `bun.lockb`) y
configuración (`tsconfig.json`, `angular.json`, `svelte.config.js`,
`components.json`, `bunfig.toml`, `next.config.*`,
`vue`/`nuxt`/`astro`/`vite`/`vitest`/`playwright`/`tailwind`). Devuelve ids
canónicos ordenados (`python`, `pytest`, `fastapi`, `django`, `react`, `nextjs`,
`tailwind`…). `npx autoskills` no forma parte del núcleo.

### Registro y caché

El registro oficial es el `index.json` de AutoSkills. Cada entrada declara
`source`, `skillPath`, `commitSha`, `files`, `sha256` por fichero y `bundleHash`
(SHA-256 de las líneas `fichero:sha256` ordenadas). Se valida: versión entera,
nombres `[a-z0-9][a-z0-9._-]*`, rutas relativas sin `..`, sin `.zip`, hashes de
64 hex y bundle coherente; una entrada inválida aborta la carga entera.

La copia vive en `.taiki/generated/skills/.cache/registry.json` (derivado,
ignorado por Git), caduca a las 24 h y se reutiliza si falla la descarga.
`TAIKI_SKILLS_REGISTRY` apunta a un registro local o espejo (tests y entornos
sin red). La descarga usa timeout de 20 s y un tope de 2 MiB por fichero.

### Sugerencias e instalación

`taiki skills suggest [--limit N] [--json]` cruza el stack con el registro y
devuelve nombre, tecnologías, fuente, commit, `bundleHash`, `review` y
`securityCheck`, sin filtrar por revisión: expone los flags para decidir.
`skills_suggest` (MCP) hace la misma consulta en solo lectura y acepta `techs`
para consultar tecnologías concretas.

`taiki skills install <nombre>` es **exclusivo del usuario/CLI**: no hay tool MCP
de instalación o retirada. Pide confirmación salvo `--yes` (obligatorio sin
TTY). Descarga el bundle de `raw.githubusercontent.com`, verifica el SHA-256 de
cada fichero y recalcula `bundleHash`, exige `SKILL.md` UTF-8 no vacío, escanea
secretos e inyección y guarda un **playbook propuesto** (`source=import`,
`status=proposed`, `M-####`) con evidencia de URL y commit.

Nada se ejecuta ni activa; se activa con `taiki memory approve M-####` (o
`reject M-#### --why "..."`). Los scripts se guardan como texto; los apoyos van
a `references/`, `scripts/`, `assets/` o `templates/` según su ruta. La
instalación **no sobrescribe** un playbook vigente: retíralo antes o instala
otra versión con aprobación.

### Listado, retirada, sync y seguridad

```bash
taiki skills list [--json]                 # instaladas, estado y origen
taiki skills remove <nombre> --why "..."   # archiva, no borra
taiki skills sync [--json]                 # exporta activos y retira obsoletos
```

`list` oculta archivados y sustituidos y distingue el origen (`autoskills` o
`local`) por la URL de evidencia. `remove` archiva y conserva el ledger. `sync`
equivale a `memory playbook export --all`: escribe
`.taiki/generated/skills/<slug>/SKILL.md` con sus apoyos, retira los de
playbooks archivados o sustituidos y solo exporta `active`; un skill recién
instalado no aparece hasta aprobarse.

- No se ejecuta ningún script: los apoyos se cargan bajo demanda. Sin Node ni
  `npx`; la red se limita al registro y al bundle.
- Rutas validadas (relativas, sin `..`, sin absolutos ni `.zip`) y escaneo de
  secretos/inyección, con el límite `max_playbook_chars` (12000 por defecto).
- El registro y los hashes son la fuente de verdad: si un SHA-256 o el
  `bundleHash` no coinciden, se aborta la instalación completa.
- `skills_suggest` es solo lectura; `install`/`remove` no se exponen por MCP.

### Desinstalación

`taiki skills remove <nombre> --why "..."` archiva el playbook, `taiki skills
sync` retira el skill exportado y `taiki mcp uninstall` quita el MCP, el brief y
la ruta de skills. También retira la entrada `.taiki/generated/skills` de
`opencode.json` y desactiva el guard. No borra el directorio generado ni la DB:
`.taiki/generated/skills/` es derivado y puedes eliminarlo sin perder playbooks.
Los playbooks archivados y su ledger se conservan; no edites la DB a mano.

## Candidatos y revisión

El motor es **determinista**: lee tickets, eventos, comentarios, enlaces, rutas,
intentos y asignaciones, y propone qué merece revisión. No usa LLM, no hace red
y **no escribe memoria**: solo prepara candidatos con evidencia.

### Reglas

| Regla | Señal | Qué propone |
|---|---|---|
| `rework` | `iterations >= 2` o reapertura desde `completado`/`verificado` | Revisar la corrección repetida. |
| `rejection` | Paso a `en_progreso` con comentario `Rechazado en revisión: …` | Recordar el criterio que faltó. |
| `blocked` | Paso por `bloqueado` o ticket bloqueado | Anticipar decisión o dependencia. |
| `expensive_close` | `iterations >= 3` | Atajar un flujo con demasiados intentos. |
| `routing` | Par `(kind, assigned_to)` con muestra suficiente | Observar qué perfil rinde mejor. |
| `hotspot` | Varios tickets con incidencias bajo el mismo prefijo de ruta | Revisar una zona. |
| `directive` | Comentario explícito (`usa`, `prefiere`, `siempre`, `nunca`…) | Persistir una instrucción durable. |

El routing exige **5 muestras** (`candidate_routing_min_samples`), calcula la
**cota inferior de Wilson** (mínimo 0.5) y solo propone si la tasa de éxito
supera la media en al menos 0.15. Los hotspots necesitan ≥2 tickets con
incidencias en el mismo prefijo. Una prohibición amplia exige 2 evidencias; una
directiva no prohibitoria basta con una.

Cada candidato se agrupa por `(regla, tipo sugerido, prefijo de ruta, motivo
normalizado)` y se deduplica. Incluye `rule`, `title`, `summary`, `evidence`
(ticket/commit), `sample_size`, `confidence`, `paths`, `suggested_key` y
`details`. Descarta fallos transitorios (timeout, flaky, reintentos de
infraestructura), secretos, narrativas de una tarea y prohibiciones amplias sin
evidencia suficiente.

### Umbrales

`candidate_min_evidence` 2 (evidencia mínima para prohibiciones y hotspots),
`candidate_lookback_days` 90 (ventana de histórico), `candidate_routing_min_samples`
5 (muestras por par `(kind, perfil)`), `candidate_max` 20 (candidatos por
consulta) y `candidate_similarity` 0.8 (similitud validada, 0 < x ≤ 1).

```bash
taiki memory candidates --since 30d --kind pitfall --limit 5
taiki memory candidates --all           # ignora candidate_max
taiki memory review --write             # .taiki/generated/memory-review.json
taiki memory pending --diff             # propuestas con diff resumido
```

`--since` acepta días (`90d`) o fecha ISO; `--kind` filtra por tipo de memoria
sugerida; `--json` (también en `candidates`) es el contrato estable para
agentes. `review --write` es atómico y `.taiki/generated/` está ignorado por
Git. `pending --diff` compara con la memoria sustituida y resume los cambios.

### MCP y curator

`memory_candidates` es de solo lectura: `mode=list` devuelve
`{count, candidates}`; `mode=review`, el paquete completo (`generated_at`,
`candidates`, `current_memories`, `recent_tickets`, `thresholds`, `rules`,
`instructions`); acepta `since`, `kind` y `limit` (20 por defecto). También
existen el recurso `taiki://memory/candidates` y el prompt `taiki:curate`.

El prompt guía al subagente **curator**: lee el paquete, propone como máximo
**5 memorias**, prefiere `patch`/`supersede` sobre `add`, exige evidencia, no
aprueba, no edita código, no lanza subagentes y no aprende de fallos
transitorios, secretos ni prohibiciones amplias.

El conductor lo lanza en background **cada 5 tickets verificados o cerrados**,
o antes si `memory_candidates(mode=list)` acumula **3 o más candidatos**, y
publica las propuestas. La aprobación sigue siendo del usuario.

## Routing y checklist

### Routing advice

`routing_advice(conn, kind, paths, config)` observa el histórico cerrado y
devuelve el perfil que mejor resolvió tareas de ese `kind`/rutas:

- exige al menos `candidate_routing_min_samples` (5 por defecto);
- calcula la **tasa de primer intento** (`iterations <= 1` y no cancelado), la
  **cota inferior de Wilson** y los **intentos medios**;
- devuelve `profile`, `variant`, `steps`, `sample`, `first_pass`, `confidence`,
  `avg_attempts` y `reason`, o `None` sin muestra;
- con `paths` solo usa tickets que tocan esas rutas; no extrapola de otras.

El MCP lo incluye en `agents` (con `kind`) y en `schedule` (una entrada por
tarea lista) dentro de `advice`. Es una sugerencia con muestra y confianza:
**nunca cambia la configuración ni lanza agentes**; el conductor decide si
aplica perfil, variante o steps. Resumen en
[Uso › Routing advice y checklist](uso.md#routing-advice-y-checklist).

### Checklist del reviewer

`memory_checklist(kind, memory_kind, paths, limit)` devuelve hasta 5 pitfalls y
convenciones `active` que casan con el cambio:

- `kind` es el **tipo de ticket** (`bug`, `funcionalidad`, `mantenimiento`…);
  filtra por `ticket_kinds`. Las memorias sin etiqueta son generales y entran
  siempre.
- `memory_kind` es opcional y filtra por **tipo de memoria** (`pitfall` o
  `convention`).
- `paths` filtra por rutas; cada entrada es compacta, con evidencia y motivo.

El reviewer pide el checklist antes de revisar, completa con `memory_search` y
propone un `pitfall` si detecta un fallo recurrente, sin aprobarlo. Los hints de
`claim` aplican la misma compatibilidad: una memoria etiquetada para otro tipo
de ticket no se sugiere.

## Presupuesto

Techo por scope, no objetivo. `active`, `proposed` y `stale` consumen
caracteres; archivadas y sustituidas no. Controlan el tamaño
`project_budget_chars` 3000 y `user_budget_chars` 1200 (cada scope),
`brief_budget_chars` 4500 (brief renderizado), `max_entry_chars` 600 (título y
contenido de una entrada), `max_playbook_chars` 12000 (cuerpo y cada apoyo) y
`playbook_index_chars` 400 (índice en el brief).

Si una escritura deja el scope por encima, por defecto **solo avisa**: con
`enforce_budget=false` (defecto) el recibo trae `budget_warning` con el total
usado y el límite, y el brief selecciona por prioridad lo que cabe. Con
`enforce_budget=true` la escritura falla con total usado, límite y entradas que
ocupan sitio; no recorta en silencio. Para liberar espacio ejecuta
`taiki memory curate`, archiva (`rm`) o edita. El curado solo recorta lo no
protegido y lo deja en el ledger. Los playbooks no consumen el presupuesto del
scope: solo su índice consume `playbook_index_chars`.

`brief_mode` decide qué lleva el brief: `full` (defecto) vuelca el contenido
destilado y `index` deja una línea compacta por memoria (código, tipo y título
con `memory_search <código>`), así el brief no es el depósito completo y el
detalle se carga bajo demanda.

Prioridad para el brief: fijadas (`pin`), scope `user`, confianza, recencia y
número de usos.

## Curado determinista

`taiki memory curate` mantiene la memoria sana sin LLM y sin borrar filas:

- `active → stale` tras más de `stale_after_days` sin uso ni actualización.
- `stale → archived` tras más de `archive_after_days` en ese estado.
- `pin` y `lock` quedan **exentos**: no se caducan, archivan ni recortan.
- **Dedupe**: agrupa por fingerprint exacto, misma clave o similitud
  (`candidate_similarity`) dentro del scope. Gana la de mayor prioridad
  (pin/lock, estado vigente y prioridad normal); la perdedora se archiva y sus
  evidencias y ficheros se absorben en la ganadora.
- **Presupuesto**: si un scope supera su límite, archiva primero las de menor
  prioridad no protegidas y reporta las retiradas; si todo está fijado, lo deja
  explícito en el informe.
- Cada transición y merge deja asiento en el ledger. `--dry-run` calcula el
  mismo plan en un savepoint y lo revierte: no cambia estados ni ledger. El
  curado nunca aprueba propuestas ni borra datos.

## Métricas e impacto

`taiki memory stats` devuelve conteos por estado, scope y kind, presupuesto por
scope, propuestas pendientes, uso (`by_action`, `use_count`, feedback y último
uso) y routing observado por `(tipo de ticket, perfil)`.

`taiki memory impact` resume cierres, intentos, lead time, verificaciones y
fallos de la ventana, por semana. Son **datos descriptivos**: una tendencia no
demuestra causalidad. No hay métricas por proveedor ni extrapolación de muestras
pequeñas.

## Salud de memoria

`health` incluye `memory` con `enabled`, `db_ok`, `entries`, `pending`, `stale`,
`budget`, `generated_synced` e `issues[]`. Comprueba tablas y FTS de memoria
sincronizados, artefactos generados (brief y skills activos) presentes, y avisa
de propuestas con más de 7 días, entradas `stale` y presupuesto excedido. No
bloquean `health` salvo `db_ok=false`, que es grave. No aprueba nada.

## Evidencia

Tipos: `ticket` (`T-0045.05`), `commit` (`3bea4b9` o hash completo), `file`
(`docs/memoria.md`), `url` (`https://…`) y `session` (`ses_…`). Una evidencia de
ticket también rellena `ticket_id`. Permite revisar de dónde salió la lección y
es obligatoria para escrituras de agente.

## Seguridad

Antes de guardar se escanean títulos, contenidos, evidencias y motivos:

- **Secretos**: claves OpenAI, tokens GitHub, claves AWS, claves privadas y
  credenciales en claro (`password: …`, `token=…`). Se rechaza la escritura y el
  mensaje no repite el valor.
- **Inyección de prompt**: frases como «ignora las instrucciones anteriores» o
  «system prompt». Se rechaza para que una memoria no sea una orden encubierta.
- **Unicode invisible y caracteres de control**: se rechazan para evitar texto
  que se lea distinto de lo que parece.
- **Rutas de ficheros**: solo relativas y dentro de `references/`, `scripts/`,
  `assets/` o `templates/`; nada de absolutos, `..` ni enlaces que salgan del
  proyecto.

Toda propuesta pasa por aprobación humana: un agente no puede aprobar ni
rechazar; el CLI exige actor `usuario`.

## Ledger, export/import y rollback

Cada alta, edición, aprobación, rechazo, archivado, fijado, feedback,
supersesión o curado deja un asiento **append-only** en `memory_ledger` con
actor, sesión, acción, motivo y hashes del estado anterior y posterior. Los
snapshots completos se guardan como blobs content-addressed. `taiki memory show
M-0001` muestra los últimos asientos y el uso.

**Exportación**. `taiki memory export <ruta>` escribe memorias, playbooks,
ficheros, evidencia, propuestas y relaciones, con `format`, `version` y un
`checksum` sha256 determinista; los secretos conocidos se redactan antes. En
JSON es reimportable; en Markdown la parte visible es un informe y el JSON
canónico viaja embebido para no perder datos.

**Importación**. `taiki memory import <ruta>` valida formato, versión, checksum
y contenido (secretos, rutas, límites) antes de escribir. Estrategias: `keep`
omite conflictos con código o clave existente, `replace` actualiza la entrada y
`new` inserta con código nuevo. `--dry-run` informa conflictos sin escribir.
Importar deja ledger y no borra filas.

**Rollback**. `taiki memory rollback <ledger-id>` restaura el estado anterior a
ese asiento y registra uno nuevo. `--snapshot <fichero>` reimporta el snapshot
en modo `replace` exacto: restaura entradas y archiva las que sobran, sin
borrarlas. Ambos admiten `--dry-run` y quedan auditados. Para retirar algo de
circulación usa `taiki memory rm` (archiva, nunca borra).

## Auto-curado y review_command (opt-in)

`memory.auto_curate` está desactivado por defecto. Con `enabled=true`, `taiki
status`, `taiki next` y `taiki memory curate` ejecutan el curado determinista
cuando toca según `interval_hours` e `idle_minutes`; `memory curate` lo fuerza.
La marca `last_run` vive en la meta de la DB y `--dry-run` no la actualiza. El
auto-curado no aprueba ni borra.

`memory.review_command` es opcional. Con él, `taiki memory review --run-command`
envía el paquete por stdin y espera JSON por stdout: una lista de operaciones o
`{"operations": [...]}`. Taiki valida la salida, aplica `review_timeout_seconds`
y convierte las altas en `proposed`. En esta versión el comando solo puede
proponer `add`, hasta 5 entradas, y no reemplaza claves existentes. El núcleo no
usa API keys ni red; el comando externo es responsabilidad de quien lo configura.

## CLI

```bash
taiki remember "Prefiero commits en español"        # scope user, preference
taiki remember "El gate exige ruff" --ticket T-0045.05
taiki memory add --scope project --kind convention \
  --title "Los docs van en docs/" --content "..." \
  --evidence T-0045.05 --evidence 3bea4b9 --pin

taiki memory list --kind pitfall --status active
taiki memory show M-0001
taiki memory search "gate exige ruff"   # lectura bajo demanda, sin brief
taiki memory search template --scope project --kind convention --limit 5 --json
taiki memory pending --diff            # diff frente a la sustituida
taiki memory approve M-0002            # solo el usuario
taiki memory reject M-0003 --why "Es un fallo transitorio"
taiki memory edit M-0001 --content "Texto corregido"
taiki memory pin M-0001
taiki memory unpin M-0001
taiki memory rm M-0001 --why "Ya no aplica"   # archiva

taiki memory curate --dry-run
taiki memory stats --json
taiki memory impact --days 30 --json
taiki memory export .taiki/generated/memoria.json
taiki memory export .taiki/generated/memoria.md --format markdown
taiki memory import .taiki/generated/memoria.json --strategy keep --dry-run
taiki memory rollback 42 --dry-run
taiki memory rollback --snapshot .taiki/generated/memoria.json
```

`memory list` excluye archivadas y sustituidas salvo que pidas ese estado;
`--json` existe en todos los subcomandos. `memory rm` archiva (no hay borrado
destructivo) y `memory curate` nunca aprueba ni borra.

## MCP

| Herramienta | Uso |
|---|---|
| `brief` | `section`, `budget`, `write`. |
| `memory_search` | Texto y filtros `scope`, `kind`, `status`, `paths`, `kinds`, `ticket_kinds`; snippets salvo `include_content=true`. |
| `memory_write` | `add`, `patch`, `remove`, `supersede`, `feedback`; `kind=playbook` acepta `description` y `files`; `ticket_kinds` etiqueta; recibo con presupuesto. |
| `memory_playbook` | Solo lectura; `path` carga un apoyo. |

`memory_checklist`, `memory_candidates`, `memory_stats` y `health` se detallan
en sus secciones; el catálogo completo, en [MCP](mcp.md#herramientas-mcp).

Recursos: `taiki://brief` (Markdown), `taiki://memory/{code}` (JSON),
`taiki://playbook/{slug}` (JSON) y `taiki://memory/candidates` (JSON).
Prompts: `taiki:brief` aplica el brief al empezar; `taiki:playbook` carga un
procedimiento y recuerda sus pitfalls; `taiki:curate` guía la revisión de
candidatos sin aprobar ni editar. Catálogo en
[MCP](mcp.md#herramientas-mcp) y [recursos y
prompts](mcp.md#recursos-y-prompts-mcp).

El recibo de `claim` puede incluir `hints`: memorias activas por rutas, tipo de
ticket o etiquetas, y hasta dos playbooks con `slug`, `title`, `description` y
`reason` para `memory_playbook`; las memorias sin `ticket_kinds` son generales y
las de otro tipo se omiten. El de `complete` puede incluir `learning`, una
sugerencia de lección tras rework o reapertura. Son ayudas, no órdenes.

## Configuración

El bloque `memory` vive en `.taiki/config.json`; las claves desconocidas fallan
al arrancar para no ignorar erratas.

```json
{
  "memory": {
    "enabled": true,
    "write_policy": "balanced",
    "project_budget_chars": 3000,
    "user_budget_chars": 1200,
    "brief_budget_chars": 4500,
    "brief_mode": "full",
    "enforce_budget": false,
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
    "auto_approve_evidence": true,
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

`enabled` apaga la memoria sin borrar datos; `write_policy` es `balanced`
(defecto) o `manual`; `auto_approve_evidence` (defecto `true`) activa las altas
de agente cuya evidencia se puede comprobar; `user_scope` fija el scope de las
preferencias (`project`)
y `skills_sync=false` evita exportar playbooks al sincronizar OpenCode.
`brief_mode` es `full` (contenido en el brief) o `index` (solo código, tipo y
título) y `enforce_budget=false` convierte el exceso de presupuesto en aviso en
vez de bloquear la escritura.
`archive_after_days >= stale_after_days` y `0` es un umbral inmediato, no un
interruptor. Resto de claves, en [Presupuesto](#presupuesto), [Umbrales](#umbrales) y
[Auto-curado](#auto-curado-y-review_command-opt-in).

## Flujo recomendado

1. Al empezar, aplica el brief y busca solo lo que falte con `memory_search`.
2. Si el ticket encaja con un playbook del índice, cárgalo con
   `memory_playbook(slug)` y sus apoyos solo cuando los necesites.
3. Trabaja con el ticket como fuente de verdad.
4. Si hubo corrección, rework o un workaround no obvio, propón una lección con
   evidencia; no guardes fallos transitorios, credenciales, narrativas ni
   prohibiciones amplias.
5. Antes de revisar, pide `memory_checklist(kind="pitfall"|"convention",
   paths=[...])` y completa con `memory_search` de pitfalls.
6. Cada 5 tickets verificados/cerrados o con 3+ candidatos, revisa
   `taiki memory candidates` y lanza el curator con `taiki memory review
   --write` (o `memory_candidates(mode=review)`); propone como máximo 5
   memorias y no aprueba ni edita código.
7. El usuario revisa con `taiki memory pending --diff` y aprueba o rechaza.
8. Refresca el brief con `taiki brief --write` si quieres actualizar el
   artefacto inyectado.
9. Mantén la memoria con `taiki memory curate` (o el auto-curado opt-in) y
   revisa `stats`/`impact`; para versionar o mover usa `memory export`/`import`
   y para deshacer, `memory rollback` por asiento o snapshot.

## FAQ

**¿Por qué no guarda transcripciones?** Una transcripción es larga, contiene
ruido y secretos accidentales y envejece mal; el objetivo es contexto
reutilizable, y el historial completo ya vive en el ticket y la sesión.

**¿Cómo actualizo el brief inyectado?** `taiki brief --write` regenera
`.taiki/generated/brief.md`; `taiki mcp install` también lo regenera y mantiene
la entrada en `instructions`.

El resto de dudas (editar, archivar, quién aprueba, qué merece una lección) se
responden en [Ciclo de vida](#ciclo-de-vida), [Candidatos y
revisión](#candidatos-y-revisión) y [Flujo recomendado](#flujo-recomendado).
