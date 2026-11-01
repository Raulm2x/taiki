#!/usr/bin/env python3
"""Umbral de cobertura por capa con ratchet.

Uso::

    coverage run tests/run.py
    coverage json -o coverage.json
    python tests/coverage_gate.py [coverage.json]

Los umbrales viven en ``pyproject.toml`` (``[tool.coverage.taiki]``) y solo se
suben: bajarlos es una decisión explícita que debe justificarse en el commit.

Qué mide y qué no:

- ``ticketlib`` y ``mcp_layer`` se miden completos (líneas y ramas).
- ``commands`` mide el CLI cuando se importa en el proceso de la suite. Los
  tests que lanzan ``bin/taiki`` como subproceso no se instrumentan, así que
  las rutas exclusivas del wrapper quedan fuera del porcentaje.
- ``tui`` excluye ``app.py`` y ``view.py`` (curses, no testeables sin terminal);
  se mide la lógica pura: ``state``, ``detail``, ``heatmap``, ``data`` y
  ``actions``.
"""

from __future__ import annotations

import argparse
import json
import tomllib
from pathlib import Path
from typing import Any, Iterable

ROOT = Path(__file__).resolve().parent.parent
CONFIG = ROOT / "pyproject.toml"


def load_thresholds(path: Path | None = None) -> tuple[dict[str, float], list[str]]:
    """Lee los umbrales por capa y los ficheros ignorados."""
    with (path or CONFIG).open("rb") as handle:
        data = tomllib.load(handle)
    section = data.get("tool", {}).get("coverage", {}).get("taiki", {})
    ignore = [str(item) for item in section.get("ignore", [])]
    thresholds = {
        str(key): float(value)
        for key, value in section.items()
        if key != "ignore" and isinstance(value, (int, float))
    }
    return thresholds, ignore


def layer_totals(
    files: dict[str, Any],
    ignore: Iterable[str] = (),
) -> dict[str, tuple[int, int]]:
    """Líneas cubiertas y sentencias por capa (primer nivel del paquete)."""
    skipped = {item.replace("\\", "/") for item in ignore}
    totals: dict[str, list[int]] = {}
    for name, info in files.items():
        path = name.replace("\\", "/")
        if path in skipped:
            continue
        layer = path.split("/", 1)[0]
        summary = info.get("summary", {})
        bucket = totals.setdefault(layer, [0, 0])
        bucket[0] += int(summary.get("covered_lines", 0))
        bucket[1] += int(summary.get("num_statements", 0))
    return {layer: (value[0], value[1]) for layer, value in totals.items()}


def percentage(covered: int, statements: int) -> float:
    return 100.0 if statements == 0 else 100.0 * covered / statements


def evaluate(
    totals: dict[str, tuple[int, int]],
    thresholds: dict[str, float],
) -> list[str]:
    """Incumplimientos por capa, con el porcentaje real y el mínimo."""
    failures: list[str] = []
    for layer, minimum in sorted(thresholds.items()):
        if layer not in totals:
            failures.append(f"{layer}: sin datos de cobertura (mínimo {minimum:.0f}%)")
            continue
        covered, statements = totals[layer]
        real = percentage(covered, statements)
        if real + 1e-9 < minimum:
            failures.append(
                f"{layer}: {real:.1f}% < {minimum:.0f}% "
                f"({covered}/{statements} líneas)"
            )
    return failures


def format_report(
    totals: dict[str, tuple[int, int]],
    thresholds: dict[str, float],
) -> list[str]:
    lines = [f"{'capa':<14} {'cobertura':>10} {'mínimo':>8} {'líneas':>14}"]
    for layer in sorted(set(totals) | set(thresholds)):
        covered, statements = totals.get(layer, (0, 0))
        minimum = thresholds.get(layer)
        mark = "" if minimum is None else f"{minimum:.0f}%"
        lines.append(
            f"{layer:<14} {percentage(covered, statements):>9.1f}% "
            f"{mark:>8} {covered:>6}/{statements:<7}"
        )
    return lines


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Umbral de cobertura por capa")
    parser.add_argument(
        "report",
        nargs="?",
        default=str(ROOT / "coverage.json"),
        help="JSON generado con `coverage json`",
    )
    parser.add_argument(
        "--config",
        default=str(CONFIG),
        help="pyproject.toml con [tool.coverage.taiki]",
    )
    args = parser.parse_args(argv)

    report = Path(args.report)
    if not report.is_file():
        print(f"No existe {report}; ejecuta `coverage json -o {report}`")
        return 2
    data = json.loads(report.read_text(encoding="utf-8"))
    thresholds, ignore = load_thresholds(Path(args.config))
    totals = layer_totals(data.get("files", {}), ignore)
    for line in format_report(totals, thresholds):
        print(line)
    failures = evaluate(totals, thresholds)
    if failures:
        print("\nCobertura por debajo del umbral:")
        for item in failures:
            print(f"  - {item}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
