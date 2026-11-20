# Changelog

## v0.5.0 — 2026-11-19

Tickets: T-0062.01, T-0062.02, T-0062.03, T-0062.05, T-0063, T-0064, T-0065.01, T-0065.02, T-0065.03, T-0065.04, T-0065.05, T-0066, T-0067.01, T-0067.02, T-0067.03, T-0067.04, T-0067.05, T-0068, T-0069, T-0070, T-0071, T-0072, T-0073, T-0074, T-0075, T-0076, T-0079, T-0078.01, T-0078.02, T-0078.03, T-0078.04, T-0078.05, T-0078.06, T-0080, T-0081, T-0082, T-0083, T-0084, T-0085.14, T-0085.15, T-0085.01, T-0085.05, T-0085.11, T-0085.17, T-0085.04, T-0085.07, T-0085.18, T-0085.02, T-0085.06, T-0085.08, T-0085.13, T-0085.12, T-0085.03, T-0085.09, T-0085.22, T-0085.20, T-0085.21, T-0085.16, T-0085.10, T-0085.19, T-0085.23, T-0088.03, T-0088.04, T-0088.05, T-0088.02, T-0088.06, T-0088.01, T-0088.07, T-0088.08, T-0089, T-0091, T-0090.01, T-0090.02

### feat
- feat: servers --reload espera a la huella nueva por PID (T-0088.08)
- feat: cerrar sin force con lease caducado o steps_exhausted (T-0088.06)
- feat: contrato del ticket en los perfiles worker (T-0088.02)
- feat: iterations y attempts en --json-summary (T-0088.05)
- feat: evidencia en transition y tool reject_delivery (T-0085.23)
- feat: delivery_rejected, evidencia estructurada y contadores unificados (T-0085.10)
- feat: --allow-root en renumber CLI (T-0085.16)
- feat: perms honesto y aviso de perfil sin reglas (T-0085.21)
- feat: servers --reload y recarga en mcp install (T-0085.20)
- feat: re-exec del MCP por SIGHUP y selección de procesos (T-0085.20)
- feat: epic_check en MCP y catálogo (T-0085.09)
- feat: epic_check deriva el cierre de las hijas (T-0085.09)
- feat: allowlist project-aware en perfiles worker (T-0085.03)
- feat: taiki perms/config show y deny explicativo (T-0085.12)
- feat: presupuesto medium a 32 pasos y política de auto-extensión (T-0085.08)
- feat: unlink y links_repair para enlaces colgantes (T-0085.06)
- feat: gate_run ejecuta gates declarados (T-0085.02)
- feat: heartbeat expone abandon y steps_exhausted (T-0085.07)
- feat: abandon y steps_exhausted en el store (T-0085.07)
- feat: alias de códigos renumerados en esquema y reorg (T-0085.04)
- feat: patch de evidence, validación y merge de memorias (T-0085.11)
- feat: retirar también refs bare muertas (T-0084)
- feat: repos adicionales y retirada de refs muertas (T-0083)
- feat: reparar evidencia con prefijo embebido (T-0082)
- feat: tools MCP de reorganizacion (T-0078.05)
- feat: purge de tickets con backup y guardrails (T-0078.04)
- feat: CLI de reorganizacion de tickets (T-0078.03)
- feat: aplicar reorganizacion y referencias (T-0078.02)
- feat: plan de reorganizacion de tickets (T-0078.01)
- feat: allowlist read-only de verificación e inspección (T-0076)
- feat: tags en memoria, índice y capa 0 (T-0072)
- feat: brief agrupado por dominio y stats índice/almacenamiento (T-0071)
- feat: brief índice con key, ticket_kinds y paths (T-0070)
- feat: merger fuera de los workspaces live (T-0068)
- feat: verificar vigencia de la evidencia al consultar memoria (T-0067.04)
- feat: búsqueda de memoria por CLI (T-0067.03)
- feat: auto-aprobar memorias con evidencia validada (T-0067.02)
- feat: brief índice y presupuesto que solo avisa (T-0067.01)

### fix
- fix: el trailer Release-Bump no degrada el nivel de la release (T-0090.01)
- fix: prune lee el PID activo de la DB del proyecto (T-0091)
- fix: prune --duplicates conserva el servidor activo identificado (T-0091)
- fix: allows de lectura git (merge-base, rev-list, cat-file, branch --contains, for-each-ref) y deny git merge afinado (T-0088.01)
- fix: renumber con allow_root actualiza el contador (T-0085.16)
- fix: alternativas del guard ejecutables por cualquier worker (T-0085.21)
- fix: mensaje de staging masivo dentro del límite (T-0085.12)
- fix: confirmar conserva completed_at y las métricas de cierre (T-0085.13)
- fix: gate_run solo ejecuta gates del ticket para su sesión (T-0085.02)
- fix: el hook resuelve alias sin romper la rama del ticket (T-0085.04)
- fix: worktree merge lista los ficheros que bloquean y conserva untracked (T-0085.05)
- fix: release usa verify.command del workspace (T-0085.01)
- fix: aceptar directorios como evidencia de fichero (T-0082)
- fix: normalizar también las refs inválidas (T-0082)
- fix: clic en AGENTE solo enfoca, no abre sesion (T-0079)
- fix: cerrar bypass de 2> en la allowlist (T-0075)
- fix: cerrar redirecciones y find/sort destructivos en workers (T-0075)
- fix: mostrar índice y almacenamiento por separado en memory stats (T-0073)
- fix: recortar título en el índice del brief (T-0070)
- fix: sync de perfiles no destructivo y --force-agents (T-0069)
- fix: permitir la rama live en tickets none/scope (T-0066)
- fix: instalar hooks en mcp install y verificación por tipo (T-0065.05)
- fix: avisar de rutas ignoradas y usar project del workspace (T-0065.04)
- fix: modo live en perfiles worker y git -C status/add (T-0065.03)
- fix: alinear permisos del conductor con el flujo live (T-0065.02)
- fix: aceptar config raíz sin bloque memory (T-0065.01)
- fix: enlazar commits de sys al ticket (T-0064)
- fix: traducir paths del ticket al repo live (T-0063)

### refactor
- refactor: catálogo MCP bajo 32 KiB (T-0088.04)
- refactor: recortar descripciones del catálogo MCP (T-0088.04)

### docs
- docs: notas de migración de v0.5.0 (T-0090.02)
- docs: política de subida del techo de checks (T-0088.07)
- docs: plantilla de delegación sin repetir el perfil (T-0088.02)
- docs: ayuda de perms sin prometer exactitud que no da (T-0088.03)
- docs: allowlist de workers para proyectos no-Python (T-0085.03)
- docs: política de presupuesto de pasos en perfiles worker (T-0085.08)
- docs: reorganizacion de tickets (T-0078.06)
- docs: memoria autónoma en la referencia MCP (T-0067.05)
- docs: memoria autónoma; el usuario solo confirma tickets (T-0067.05)

### test
- test: cubrir el bump que no degrada y documentar la semántica (T-0090.01)
- test: duplicados conservan al activo por evidencia y --force explicita (T-0091)
- test: regresion de la etiqueta tiempo/fallo en la evidencia (T-0089)
- test: espera de recarga con reloj inyectado y doc de estados (T-0088.08)
- test: comprobar un comando real de merge, no el patrón (T-0088.01)
- test: allowlist worker valida lecturas git nuevas y denies intactos; docs de operacion con tabla por perfil (T-0088.01)
- test: leer el evento del cierre, no el del reclamo (T-0088.06)
- test: contrato de iterations/attempts en resumen CLI y MCP (T-0088.05)
- test: contrato de evidence y reject_delivery (T-0085.23)
- test: bajar baseline_checks a 1567 tras la poda (T-0085.19)
- test: podar la suite sin perder cobertura (T-0085.19)
- test: consolidar aserciones de auditoría bajo el techo (T-0085.10)
- test: rechazos y steps_exhausted sin doble conteo (T-0085.10)
- test: consolidar aserciones de re-exec bajo el techo (T-0085.20)
- test: re-exec y selección de procesos (T-0085.20)
- test: contrato de perms, deny explicativo y config show (T-0085.12)
- test: máquina de estados con abandon (T-0085.07)
- test: aserciones de esquema sin versión fija (T-0085.04)
- test: alias de renumber en hook y raíces libres (T-0085.04)
- test: tiempo y re-baseline de la suite (T-0062.05)
- test: poda de MCP, skills y agentes (T-0062.03)
- test: poda de TUI y CLI (T-0062.02)
- test: poda de tests de memoria (T-0062.01)

### chore
- chore: margen de 10 checks para integrar .20 y .21 (T-0085.22)
- chore: techo de checks a 1705 para la tanda final (T-0085.18)
- chore: techo de checks a 1660 por el esquema v9 (T-0085.17)
- chore: re-basar checks y techo de módulo de la suite (T-0085.15)
- chore: re-basar el presupuesto de tiempo de la suite (T-0085.14)
- chore: bloque Unreleased para la release (T-0081)
- chore: techo de checks para la integracion (T-0080)
- chore: calibrar benchmark export 20k (T-0074)

### bug
- bug: suite=tiempo para fallos solo de reloj y techo 14.0s medido (T-0089)

## v0.4.1 — 2026-11-03

Tickets: T-0061

### fix
- fix: cerrar stdin del gate de release (T-0061)

## v0.4.0 — 2026-11-03

Tickets: T-0059, T-0060.01, T-0060.02, T-0060.03, T-0060.04, T-0060.05, T-0060.09, T-0060.10, T-0060.11, T-0060.12, T-0060.07, T-0060.08

### feat
- feat: perfil system-admin y flujo live (T-0060.07)
- feat: reject --revert en modo live (T-0060.11)
- feat: revisión especial de tickets sensibles (T-0060.10)
- feat: riesgo y autorización previa (T-0060.09)
- feat: sys export y audit (T-0060.05)
- feat: sys snapshot y reversal (T-0060.05)
- feat: sys status y helpers de sistema (T-0060.05)
- feat: claim y schedule sin worktree (T-0060.04)
- feat: commit live y enlace cross-repo (T-0060.03)
- feat: guard mode-aware y scope (T-0060.02)
- feat: modelo de aislamiento y scope (T-0060.01)

### fix
- fix: confirmación del usuario como revisión especial (T-0060.12)
- fix: propagar cwd al guard (T-0060.02)
- fix: cerrar bypasses del guard en scope (T-0060.02)
- fix: reconciliar versiones y marcador de release (T-0059)

### docs
- docs: aislamiento configurable y agente de sistema (T-0060.08)
- docs: reconciliación y marcador de release (T-0059)

### chore
- chore: margen de suite para el ledger de sistema (T-0060.05)
- chore: reconciliar develop tras v0.3.0 [sin-ticket]

## v0.3.0 — 2026-11-03

Tickets: T-0045.01, T-0045.02, T-0045.03, T-0045.04, T-0045.05, T-0045.06, T-0050, T-0046.01, T-0046.02, T-0046.03, T-0046.04, T-0046.05, T-0046.06, T-0046.07, T-0047.01, T-0047.02, T-0047.03, T-0047.04, T-0047.05, T-0047.06, T-0052, T-0051, T-0053, T-0048.01, T-0048.02, T-0048.03, T-0048.04, T-0048.05, T-0048.06, T-0049.01, T-0049.02, T-0049.03, T-0054, T-0055, T-0056, T-0057, T-0058.01, T-0058.02, T-0058.03, T-0058.04, T-0058.05, T-0058.06

### feat
- feat: versión única alineada con el release (T-0058.01)
- feat: subagente merger para integrar worktrees (T-0056)
- feat: TUI de memoria (T-0055)
- feat: CLI y MCP de skills curadas (T-0049.02)
- feat: núcleo autoskills (T-0049.01)
- feat: auto-curado y review_command P4 (T-0048.05)
- feat: export/import y rollback P4 (T-0048.04)
- feat: MCP stats y health de memoria P4 (T-0048.03)
- feat: CLI de curado, stats e impact P4 (T-0048.02)
- feat: curado determinista P4 (T-0048.01)
- feat: visibilidad de propuestas de memoria (T-0053)
- feat: ticket_kinds y checklist por tipo de ticket (T-0052)
- feat: playbooks en brief y hints P3 (T-0047.05)
- feat: routing advice y checklist P3 (T-0047.04)
- feat: MCP de playbooks y sync de skills P3 (T-0047.03)
- feat: CLI de playbooks P3 (T-0047.02)
- feat: núcleo de playbooks y export a skills P3 (T-0047.01)
- feat: subagente curator y cadencia P2 (T-0046.04)
- feat: MCP de candidatos y prompt curate P2 (T-0046.03)
- feat: CLI de candidatos y review P2 (T-0046.02)
- feat: motor de candidatos P2 (T-0046.01)
- feat: brief generado y sync OpenCode P1 (T-0045.04)
- feat: CLI de memoria y brief P1 (T-0045.02)
- feat: memoria P0, esquema v5 y store (T-0045.01)

### fix
- fix: auditoría sin épicas en confirmados sin commit (T-0058.03)
- fix: cerrar bypass de flags en el merger (T-0056)
- fix: aceptar nombres camelCase de autoskills (T-0054)
- fix: aislar worktree_create con TAIKI_DB (T-0050)
- fix: corregir hallazgos de revisión P0 memoria (T-0045.01)

### refactor
- refactor: partir CLI y export de memoria (T-0058.05)
- refactor: partir MCP por dominio (T-0058.04)
- refactor: partir candidates.py en reglas y salida (T-0051)

### docs
- docs: línea base de rendimiento y contexto (T-0058.06)
- docs: CHANGELOG v0.3.0 sin Unreleased huérfano (T-0058.02)
- docs: compactar guía de memoria (T-0057)
- docs: skills curadas de AutoSkills y desinstalación (T-0049.03)
- docs: curado, métricas y export P4 (T-0048.06)
- docs: playbooks, routing y checklist P3 (T-0047.06)
- docs: candidatos, review y curator P2 (T-0046.05)
- docs: memoria y flujo del Conductor P1 (T-0045.05)

### test
- test: mensajes únicos en candidatos P2 (T-0046.06)
- test: cobertura MCP de memoria P1 (T-0045.03)

### chore
- chore: consolidar presupuesto P2 (T-0046.07)
- chore: presupuesto de checks tras integrar P1 (T-0045.06)

## v0.2.0 — 2026-11-02

Tickets: T-0038, T-0039, T-0042, T-0040, T-0043, T-0041, T-0044

### feat
- feat: trailer Release-Bump para clasificar features pequeñas (T-0041)
- feat: taiki release --push publica develop, main y el tag (T-0040)

### fix
- fix: permitir git rebase a los workers (T-0044)
- fix: taiki commit concatena varios -m como párrafos (T-0043)
- fix: margen de tiempo de la suite y gate de merge con perfil CI (T-0042)
- fix: marcador de release para no repetir commits publicados (T-0039)
- fix: render correcto del aviso de push protegido (T-0038)
- fix: permitir push de la release a main (T-0038)

## v0.1.0 — 2026-11-02

Tickets: T-0035, T-0031.06, T-0036, T-0033, T-0037

### feat
- feat: permitir resolución en tickets confirmados (T-0033)

### fix
- fix: mensajes claros de sync/push en taiki release (T-0037)
- fix: release reconcilia develop con main tras el squash (T-0036)
- fix: poda del presupuesto y gate estricto en el merge (T-0035)

### test
- test: E2E agent → develop → release → main (T-0031.06)
