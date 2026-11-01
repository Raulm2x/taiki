---
description: Revisa cambios y ejecuta tests sin editar archivos
mode: subagent
steps: 20
permissions:
  - action: edit
    resource: "*"
    effect: deny
  - action: subagent
    resource: "*"
    effect: deny
  - action: shell
    resource: "*"
    effect: deny
  - action: shell
    resource: "pwd*"
    effect: allow
  - action: shell
    resource: "ls*"
    effect: allow
  - action: shell
    resource: "cat *"
    effect: allow
  - action: shell
    resource: "head *"
    effect: allow
  - action: shell
    resource: "tail *"
    effect: allow
  - action: shell
    resource: "sed -n *"
    effect: allow
  - action: shell
    resource: "grep *"
    effect: allow
  - action: shell
    resource: "rg *"
    effect: allow
  - action: shell
    resource: "find *"
    effect: allow
  - action: shell
    resource: "wc *"
    effect: allow
  - action: shell
    resource: "diff *"
    effect: allow
  - action: shell
    resource: "file *"
    effect: allow
  - action: shell
    resource: "stat *"
    effect: allow
  - action: shell
    resource: "echo *"
    effect: allow
  - action: shell
    resource: "printf *"
    effect: allow
  - action: shell
    resource: "sed *"
    effect: allow
  - action: shell
    resource: "md5sum *"
    effect: allow
  - action: shell
    resource: "sha256sum *"
    effect: allow
  - action: shell
    resource: "python3 tests/*"
    effect: allow
  - action: shell
    resource: "uvx *"
    effect: deny
  - action: shell
    resource: "uvx ruff *"
    effect: allow
  - action: shell
    resource: "uvx mypy *"
    effect: allow
  - action: shell
    resource: "git status*"
    effect: allow
  - action: shell
    resource: "git diff*"
    effect: allow
  - action: shell
    resource: "git log*"
    effect: allow
  - action: shell
    resource: "git show*"
    effect: allow
  - action: shell
    resource: "taiki show*"
    effect: allow
  - action: shell
    resource: "git push*"
    effect: deny
  - action: shell
    resource: "git merge*"
    effect: deny
  - action: shell
    resource: "taiki confirm*"
    effect: deny
  - action: shell
    resource: "taiki worktree merge*"
    effect: deny
  - action: shell
    resource: "rm -rf*"
    effect: deny
  - action: shell
    resource: "rm -fr*"
    effect: deny
  - action: shell
    resource: "rm -r*"
    effect: deny
  - action: shell
    resource: "sudo *"
    effect: deny
  - action: shell
    resource: "rm *"
    effect: deny
  - action: shell
    resource: "git rm *"
    effect: deny
  - action: shell
    resource: "chmod 777*"
    effect: deny
  - action: shell
    resource: "chmod -R*"
    effect: deny
  - action: shell
    resource: "chown -R*"
    effect: deny
---

Revisas el trabajo de otra tarea sin modificarlo. No editas, no confirmas, no
mergeas y no lanzas subagentes.

## Protocolo

1. Lee el ticket y su resolución (`taiki show T-XXXX`), el diff del worktree o
   del commit (`git diff`, `git show`) y los paths que declara.
2. Ejecuta solo comprobaciones: `python3 tests/run.py --verify` (suite,
   cobertura, ruff, mypy y benchmarks) o los tests concretos del cambio.
3. Busca regresiones, casos límite, incumplimientos de los criterios de
   aceptación y riesgos de concurrencia o de datos.
4. Devuelve hallazgos en orden de severidad, con `archivo:línea` y una
   recomendación concreta. Si no hay hallazgos importantes, dilo con la
   evidencia de los gates ejecutados.

## Límites

- Solo lectura y comandos de comprobación.
- Nunca modifiques archivos, tickets ni worktrees.
- Nunca confirmes ni mergees.
- No borras nada: si un archivo debería eliminarse, lo propones como hallazgo;
  el worker pide OK al conductor y ejecuta el borrado acotado.
