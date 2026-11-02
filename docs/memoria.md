# Memoria

Volver a [README](../README.md) · [Índice de documentación](README.md)

Taiki puede destilar lo aprendido en un proyecto en **memorias pequeñas y
revisables**. El objetivo no es guardar conversaciones: es recuperar contexto
operativo al empezar una tarea sin arrastrar todo el historial.

- **CLI**: `taiki memory`, `taiki remember`, `taiki brief`,
  `taiki memory candidates` y `taiki memory review`.
- **MCP**: `brief`, `memory_search`, `memory_write`, `memory_candidates`, el
  recurso `taiki://brief`, la plantilla `taiki://memory/{code}`, el recurso
  `taiki://memory/candidates` y los prompts `taiki:brief` y `taiki:curate`.
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
- `## Pendientes`: propuestas que aún no se han aprobado;
- `## Avisos`: entradas `stale` y cuántas se omitieron por presupuesto.

El encabezado dice «datos, no instrucciones»: el brief informa, pero las
reglas del proyecto y el ticket siguen mandando. Si una entrada no cabe, se
omite y el brief invita a usar `memory_search`.

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
siendo exclusiva del usuario. P2 no incluye curado automático, playbooks ni
métricas de impacto: el curator solo propone y el usuario decide.

## Presupuesto

El presupuesto es un techo por scope, no un objetivo. Las entradas `active`,
`proposed` y `stale` consumen caracteres; las archivadas y sustituidas no.

| Clave | Defecto | Qué controla |
|---|---:|---|
| `project_budget_chars` | 3000 | Total del scope `project`. |
| `user_budget_chars` | 1200 | Total del scope `user`. |
| `brief_budget_chars` | 4500 | Tamaño del brief renderizado. |
| `max_entry_chars` | 600 | Título y contenido de una entrada. |
| `max_playbook_chars` | 12000 | Ficheros de referencia asociados. |

Si una escritura deja el scope por encima del límite, falla con el total usado,
el límite y las entradas que ocupan sitio; no recorta en silencio. Para liberar
espacio, archiva (`rm`) o edita entradas.

Prioridad de selección para el brief: fijadas (`pin`) primero, luego scope
`user`, después confianza, recencia y número de usos.

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

## Ledger y rollback

Cada alta, edición, aprobación, rechazo, archivado, fijado, feedback o
supersesión deja un asiento **append-only** en `memory_ledger`, con actor,
sesión, acción, motivo y hashes del estado anterior y posterior. Los snapshots
completos se guardan como blobs content-addressed.

`taiki memory show M-0001` muestra los últimos asientos y el uso de la entrada.
La API interna `rollback` restaura el estado anterior a un asiento y registra
un asiento nuevo; en P1 el CLI expone el historial, no un comando de rollback.
Para retirar algo de circulación usa `taiki memory rm` (archiva, nunca borra).

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

# Mantenimiento
taiki memory edit M-0001 --content "Texto corregido"
taiki memory pin M-0001
taiki memory unpin M-0001
taiki memory rm M-0001 --why "Ya no aplica"
```

`memory list` excluye archivadas y sustituidas salvo que pidas ese estado.
`--json` está disponible en todos los subcomandos. `memory rm` archiva: no hay
borrado destructivo.

## MCP

| Herramienta | Uso |
|---|---|
| `brief` | Brief operativo; `section`, `budget` y `write`. |
| `memory_search` | Busca por texto y filtra por `scope`, `kind`, `status`, `paths` o `kinds`; devuelve snippets salvo `include_content=true`. |
| `memory_write` | Aplica operaciones `add`, `patch`, `remove`, `supersede` o `feedback` con evidencia y la política del workspace; devuelve un recibo con presupuesto y avisos. |
| `memory_candidates` | Solo lectura: `mode=list` lista candidatos; `mode=review` devuelve el paquete del curator. Acepta `since`, `kind` y `limit`. |

Recursos: `taiki://brief` (Markdown), `taiki://memory/{code}` (JSON) y
`taiki://memory/candidates` (JSON).
Prompts: `taiki:brief`, que aplica el brief al empezar el trabajo, y
`taiki:curate`, que guía la revisión de candidatos sin aprobar ni editar.

El recibo de `claim` puede incluir `hints`: memorias activas relevantes por
rutas, tipo o etiquetas. El recibo de `complete` puede incluir `learning`: una
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
    "stale_after_days": 45,
    "archive_after_days": 120,
    "candidate_min_evidence": 2,
    "candidate_lookback_days": 90,
    "candidate_routing_min_samples": 5,
    "candidate_max": 20,
    "candidate_similarity": 0.8,
    "instructions_sync": true,
    "skills_sync": true,
    "user_scope": "project"
  }
}
```

- `enabled`: apaga la memoria sin borrar datos.
- `write_policy`: `balanced` o `manual`.
- `user_scope`: scope por defecto de las preferencias de usuario.
- `stale_after_days` y `archive_after_days`: umbrales validados de caducidad;
  deben cumplir `archive_after_days >= stale_after_days` y `0` los desactiva.
  P1 no ejecuta una caducidad automática.
- `max_playbook_chars`: límite de los ficheros de referencia internos; P1 no
  los expone por CLI ni MCP.
- `candidate_*`: controlan el motor de candidatos (evidencia mínima, ventana,
  muestras de routing, máximo y similitud); se validan al arrancar y se aplican
  en `memory candidates` y `memory_candidates`.
- `instructions_sync` y `skills_sync` se validan como parte del bloque.

## Flujo recomendado

1. Al empezar, aplica el brief y busca solo el contexto que falte con
   `memory_search`.
2. Trabaja con el ticket como fuente de verdad.
3. Si hubo corrección, rework o un workaround no obvio, propón una lección con
   evidencia. No guardes fallos transitorios, credenciales, narrativas ni
   prohibiciones amplias.
4. Cada 5 tickets verificados/cerrados o con 3 o más candidatos, revisa
   `taiki memory candidates` y lanza el curator con
   `taiki memory review --write` (o `memory_candidates(mode=review)` por MCP).
   El curator propone como máximo 5 memorias; no aprueba ni edita código.
5. El usuario revisa con `taiki memory pending --diff` y aprueba o rechaza.
6. Refresca el brief con `taiki brief --write` si quieres actualizar el
   artefacto inyectado.

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
