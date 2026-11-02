# Changelog

## Unreleased

Tickets: T-0045.01–T-0045.05, T-0046.01–T-0046.05

### feat
- feat: memoria semántica P0/P1 con brief, búsqueda, propuestas y aprobación (T-0045)
- feat: candidatos deterministas, CLI/MCP de revisión y subagente curator P2 (T-0046)

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
