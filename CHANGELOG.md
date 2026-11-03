# Changelog

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
