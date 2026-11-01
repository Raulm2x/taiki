"""Auditoría, presupuesto y cuarentena de la suite.

Este módulo no modifica pruebas: solo mide y avisa. La cuarentena permite
aparcar un módulo inestable con dueño y caducidad; al caducar, el runner falla
para forzar una decisión.
"""

from __future__ import annotations

import ast
import tomllib
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any, Iterable

CONFIG = Path(__file__).resolve().parent
ASSERTIONS = {"ok", "eq", "raises"}

DEFAULT_BUDGET: dict[str, Any] = {
    "suite": {
        "max_seconds": 2.0,
        "baseline_checks": 0,
        "checks_tolerance": 20,
    },
    "module": {"max_seconds": 0.5},
}


@dataclass
class ModuleSource:
    """Lo que se puede saber de un módulo sin ejecutarlo."""

    name: str
    checks: int = 0
    lines: int = 0
    unused_imports: list[str] = field(default_factory=list)
    messages: list[tuple[str, int]] = field(default_factory=list)
    unsafe_skips: list[str] = field(default_factory=list)


def load_toml(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    with path.open("rb") as handle:
        return tomllib.load(handle)


def load_budget(path: Path | None = None) -> dict[str, Any]:
    """Presupuesto con valores por defecto si el fichero no existe."""
    data = load_toml(path or CONFIG / "budget.toml")
    budget = {
        "suite": dict(DEFAULT_BUDGET["suite"]),
        "module": dict(DEFAULT_BUDGET["module"]),
    }
    for section in ("suite", "module"):
        budget[section].update(data.get(section, {}))
    return budget


def load_quarantine(path: Path | None = None) -> dict[str, dict[str, Any]]:
    data = load_toml(path or CONFIG / "quarantine.toml")
    modules = data.get("modules", {})
    return dict(modules) if isinstance(modules, dict) else {}


def quarantine_problem(
    name: str,
    entry: dict[str, Any] | None,
    today: date | None = None,
) -> str | None:
    """Motivo por el que una entrada de cuarentena no es válida, o ``None``."""
    if not entry:
        return None
    missing = [
        key
        for key in ("reason", "owner", "expires")
        if not str(entry.get(key) or "").strip()
    ]
    if missing:
        return f"cuarentena incompleta para {name}: faltan {', '.join(missing)}"
    try:
        expires = date.fromisoformat(str(entry["expires"]))
    except ValueError:
        return f"cuarentena de {name}: fecha inválida {entry['expires']!r}"
    current = today or date.today()
    if current > expires:
        owner = entry.get("owner")
        return (
            f"cuarentena caducada para {name} el {expires.isoformat()} "
            f"(dueño: {owner}); revisa, arregla o elimina el módulo"
        )
    return None


def unused_imports(path: Path) -> list[str]:
    """Nombres importados que el módulo no usa."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    imported: dict[str, int] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                imported[(alias.asname or alias.name).split(".")[0]] = node.lineno
        elif isinstance(node, ast.ImportFrom):
            for alias in node.names:
                if alias.name != "*":
                    imported[alias.asname or alias.name] = node.lineno
    used = {node.id for node in ast.walk(tree) if isinstance(node, ast.Name)}
    return sorted(
        name
        for name in imported
        if name not in used and name != "annotations"
    )


def _message(call: ast.Call) -> tuple[str, int] | None:
    index = 1 if call.func.attr == "ok" else 2
    if len(call.args) <= index:
        return None
    argument = call.args[index]
    if isinstance(argument, ast.Constant) and isinstance(argument.value, str):
        return argument.value, call.lineno
    return None


def _reasoned(call: ast.Call) -> bool:
    for keyword in call.keywords:
        if keyword.arg == "reason" and isinstance(keyword.value, ast.Constant):
            return bool(str(keyword.value.value).strip())
    for argument in call.args:
        if isinstance(argument, ast.Constant):
            return bool(str(argument.value).strip())
    return False


def unsafe_skips(tree: ast.AST) -> list[str]:
    """Skip/xfail sin motivo: decoradores o llamadas sueltas."""
    found: list[str] = []
    skip_names = {"skip", "skipIf", "skipUnless", "xfail"}
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            for decorator in node.decorator_list:
                target = decorator.func if isinstance(decorator, ast.Call) else decorator
                name = (
                    target.attr
                    if isinstance(target, ast.Attribute)
                    else target.id
                    if isinstance(target, ast.Name)
                    else ""
                )
                if name in skip_names and not (
                    isinstance(decorator, ast.Call) and _reasoned(decorator)
                ):
                    found.append(node.name)
        elif isinstance(node, ast.Call) and isinstance(node.func, (ast.Attribute, ast.Name)):
            name = (
                node.func.attr
                if isinstance(node.func, ast.Attribute)
                else node.func.id
            )
            if name in {"skip", "xfail"} and not _reasoned(node):
                found.append(f"línea {node.lineno}")
    return sorted(dict.fromkeys(found))


def scan_source(name: str, path: Path | None = None) -> ModuleSource:
    """Cuenta comprobaciones fuente, líneas, imports muertos y mensajes."""
    target = path or CONFIG / f"{name}.py"
    text = target.read_text(encoding="utf-8")
    tree = ast.parse(text)
    source = ModuleSource(name=name, lines=len(text.splitlines()))
    source.unused_imports = unused_imports(target)
    source.unsafe_skips = unsafe_skips(tree)
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr in ASSERTIONS
        ):
            source.checks += 1
            message = _message(node)
            if message is not None:
                source.messages.append(message)
    return source


def seed_names(path: Path | None = None) -> list[str]:
    target = path or CONFIG / "seeds.py"
    tree = ast.parse(target.read_text(encoding="utf-8"))
    return [
        node.name
        for node in tree.body
        if isinstance(node, ast.FunctionDef) and not node.name.startswith("_")
    ]


def used_names(paths: Iterable[Path]) -> set[str]:
    used: set[str] = set()
    for path in paths:
        tree = ast.parse(path.read_text(encoding="utf-8"))
        used.update(
            node.id for node in ast.walk(tree) if isinstance(node, ast.Name)
        )
    return used


def duplicate_messages(sources: list[ModuleSource]) -> dict[str, list[str]]:
    """Mensajes de comprobación repetidos (dentro de un módulo o entre módulos)."""
    locations: dict[str, list[str]] = {}
    for source in sources:
        for message, _ in source.messages:
            locations.setdefault(message, []).append(source.name)
    return {
        message: where
        for message, where in locations.items()
        if len(where) > 1
    }


def audit(module_names: Iterable[str]) -> dict[str, Any]:
    """Informe de la suite: fuentes, duplicados, seeds y línea base."""
    names = list(module_names)
    sources = [scan_source(name) for name in names]
    duplicates = duplicate_messages(sources)
    all_tests = sorted(CONFIG.glob("test_*.py"))
    test_used = used_names(all_tests)
    unused_seeds = [name for name in seed_names() if name not in test_used]
    return {
        "sources": sources,
        "duplicates": duplicates,
        "unused_seeds": unused_seeds,
        "baseline": load_budget()["suite"],
    }


def format_audit(
    report: dict[str, Any],
    results: list[dict[str, Any]],
    partial: bool = False,
) -> list[str]:
    """Líneas legibles del informe de auditoría."""
    lines = ["Auditoría de la suite", ""]
    lines.append(f"{'módulo':<20} {'fuente':>7} {'checks':>7} {'tiempo':>8} {'imports':>8}")
    by_name = {row["name"]: row for row in results}
    for source in report["sources"]:
        row = by_name.get(source.name, {})
        checks = row.get("checks", 0)
        elapsed = row.get("elapsed", 0.0)
        lines.append(
            f"{source.name:<20} {source.checks:>7} {checks:>7} {elapsed:>7.2f}s "
            f"{len(source.unused_imports):>8}"
        )
    duplicates = report["duplicates"]
    unused = report["unused_seeds"]
    lines.append("")
    lines.append(f"Duplicados de mensaje: {len(duplicates)}")
    for message, where in list(duplicates.items())[:10]:
        lines.append(f"  - {message!r} en {', '.join(sorted(set(where)))}")
    lines.append(f"Seeds sin uso: {len(unused)}")
    if unused:
        lines.append(f"  - {', '.join(unused)}")
    dead = [source.name for source in report["sources"] if source.checks == 0]
    lines.append(f"Sin aserciones: {len(dead)}")
    if dead:
        lines.append(f"  - {', '.join(dead)}")
    skips = [
        f"{source.name}: {name}"
        for source in report["sources"]
        for name in source.unsafe_skips
    ]
    lines.append(f"Skip/xfail sin motivo: {len(skips)}")
    if skips:
        lines.append(f"  - {', '.join(skips)}")
    baseline = int(report["baseline"].get("baseline_checks", 0) or 0)
    tolerance = int(report["baseline"].get("checks_tolerance", 0) or 0)
    total = sum(row.get("checks", 0) for row in results)
    if partial:
        lines.append("")
        lines.append(f"Ejecución parcial: {len(results)} módulos, {total} comprobaciones")
        return lines
    delta = total - baseline
    lines.append("")
    lines.append(
        f"Línea base: {baseline} · real: {total} ({delta:+d}, tolerancia ±{tolerance})"
    )
    return lines


def budget_warnings(
    results: list[dict[str, Any]],
    budget: dict[str, Any],
    partial: bool = False,
) -> list[str]:
    """Avisos accionables por tiempo o desviación de la línea base."""
    warnings: list[str] = []
    suite = budget["suite"]
    module_limit = float(budget["module"].get("max_seconds", 0))
    total_time = sum(row.get("elapsed", 0.0) for row in results)
    total_checks = sum(row.get("checks", 0) for row in results)
    max_suite = float(suite.get("max_seconds", 0))
    if not partial and max_suite and total_time > max_suite:
        warnings.append(
            f"suite en {total_time:.2f}s supera el presupuesto de {max_suite:.2f}s"
        )
    if module_limit:
        for row in results:
            if row.get("elapsed", 0.0) > module_limit:
                warnings.append(
                    f"{row['name']} tarda {row['elapsed']:.2f}s "
                    f"(máximo {module_limit:.2f}s)"
                )
    baseline = int(suite.get("baseline_checks", 0) or 0)
    tolerance = int(suite.get("checks_tolerance", 0) or 0)
    if not partial and baseline and abs(total_checks - baseline) > tolerance:
        warnings.append(
            f"{total_checks} comprobaciones frente a una línea base de {baseline} "
            f"(±{tolerance}); justifica el cambio"
        )
    return warnings
