#!/usr/bin/env python3
"""Ejecutor de la suite: un módulo por área, base de datos temporal propia."""

from __future__ import annotations

import argparse
import importlib
import sys
import time
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tests import hygiene
from tests.support import Harness

MODULES = (
    "test_core",
    "test_tags_scope",
    "test_store",
    "test_scratch",
    "test_mcp",
    "test_mcp_parallel",
    "test_sessions",
    "test_cli",
    "test_tui_state",
    "test_tui_render",
    "test_hierarchy",
    "test_guard",
    "test_claims",
    "test_commit",
    "test_status",
    "test_parallel",
    "test_servers",
    "test_herdr",
    "test_githook",
    "test_hygiene",
    "test_worktrees",
    "test_workspace",
)


def _selected(only: list[str], keyword: str | None) -> list[str]:
    names = list(MODULES)
    if only:
        wanted = []
        for item in only:
            matches = [name for name in names if item == name or item in name]
            if not matches:
                raise SystemExit(f"Módulo desconocido: {item}")
            wanted.extend(matches)
        names = [name for name in names if name in wanted]
    if keyword:
        names = [name for name in names if keyword.lower() in name.lower()]
    return names


def _run_module(name: str) -> dict:
    """Ejecuta un módulo y devuelve comprobaciones, error y duración."""
    module = importlib.import_module(f"tests.{name}")
    harness = Harness()
    started = time.monotonic()
    error: str | None = None
    try:
        with harness:
            module.run(harness)
    except AssertionError as exc:
        error = str(exc)
    except Exception as exc:  # noqa: BLE001 - la suite debe seguir con el resto
        frames = traceback.extract_tb(exc.__traceback__)
        where = (
            f"{Path(frames[-1].filename).name}:{frames[-1].lineno}"
            if frames
            else "?"
        )
        error = f"{type(exc).__name__}: {exc} ({where})"
    return {
        "name": name,
        "checks": harness.checks,
        "error": error,
        "elapsed": time.monotonic() - started,
    }


def _print_warnings(warnings: list[str]) -> None:
    for warning in warnings:
        print(f"AVISO: {warning}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Suite del sistema de tickets")
    parser.add_argument(
        "modules",
        nargs="*",
        help="módulos a ejecutar (por defecto, todos)",
    )
    parser.add_argument("--only", action="append", default=[], help="ejecutar solo este módulo")
    parser.add_argument("-k", "--keyword", help="filtrar módulos por nombre")
    parser.add_argument("--list", action="store_true", help="listar los módulos")
    parser.add_argument("--failfast", action="store_true", help="parar en el primer fallo")
    parser.add_argument(
        "--audit",
        action="store_true",
        help="informe de ruido: duplicados, imports, seeds y línea base",
    )
    parser.add_argument("-v", "--verbose", action="store_true", help="mostrar cada módulo")
    args = parser.parse_args(argv)

    only = list(args.only) + list(args.modules)
    names = _selected(only, args.keyword)
    if args.list:
        for name in names:
            print(name)
        return 0

    partial = len(names) != len(MODULES)
    budget = hygiene.load_budget()
    quarantine = hygiene.load_quarantine()
    failures: list[str] = []
    results: list[dict] = []
    started = time.monotonic()

    for name in quarantine:
        if name not in MODULES:
            failures.append(f"{name}: cuarentena para un módulo desconocido")

    for name in names:
        entry = quarantine.get(name)
        problem = hygiene.quarantine_problem(name, entry)
        if problem:
            failures.append(problem)
        row = _run_module(name)
        row["quarantined"] = bool(entry and not problem)
        results.append(row)
        if row["error"] is not None:
            if row["quarantined"]:
                print(f"CUARENTENA {name}: {row['error']}")
            else:
                failures.append(f"{name}: {row['error']}")
                print(f"FALLO {name}: {row['error']}")
                if args.failfast:
                    break
        elif args.verbose:
            suffix = " (cuarentena)" if row["quarantined"] else ""
            print(
                f"  {name:<18} {row['checks']:>3} comprobaciones  "
                f"{row['elapsed']:.2f}s{suffix}"
            )

    total = sum(row["checks"] for row in results)
    elapsed = time.monotonic() - started
    warnings = hygiene.budget_warnings(results, budget, partial=partial)

    if args.audit:
        report = hygiene.audit(names)
        for line in hygiene.format_audit(report, results, partial=partial):
            print(line)
        _print_warnings(warnings)
        for failure in failures:
            print(f"FALLO {failure}")
        if not failures:
            print(f"\nOK: {total} comprobaciones ({len(names)} módulos, {elapsed:.2f}s)")
        return 0

    _print_warnings(warnings)
    if failures:
        print(f"\n{len(failures)} incidencia(s) en {elapsed:.2f}s")
        for item in failures:
            print(f"  - {item}")
        return 1
    print(f"OK: {total} comprobaciones ({len(names)} módulos, {elapsed:.2f}s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
