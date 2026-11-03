"""Auditoría, presupuesto y cuarentena de la suite.

Este módulo no modifica pruebas: solo mide y avisa. La cuarentena permite
aparcar un módulo inestable con dueño y caducidad; al caducar, el runner falla
para forzar una decisión.
"""

from __future__ import annotations

import ast
import os
import tomllib
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any, Iterable

CONFIG = Path(__file__).resolve().parent
ASSERTIONS = {"ok", "eq", "raises"}

# Texto y AST por fichero, cacheados por (mtime_ns, tamaño). La auditoría
# escanea cada módulo dos veces (fuentes y nombres usados); sin caché se paga
# el parseo dos veces y el informe se lleva casi un segundo de la suite.
_PARSE_CACHE: dict[Path, tuple[tuple[int, int], str, ast.Module]] = {}

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


def discover_modules(path: Path | None = None) -> tuple[str, ...]:
    """Módulos de prueba descubiertos en ``tests/test_*.py``."""
    folder = path or CONFIG
    return tuple(sorted(item.stem for item in folder.glob("test_*.py")))


def load_toml(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    with path.open("rb") as handle:
        return tomllib.load(handle)


def _truthy(value: Any) -> bool:
    return str(value or "").strip().lower() in ("1", "true", "yes", "on")


def _float_env(environ: dict[str, str], name: str) -> float | None:
    raw = environ.get(name)
    if raw is None or not str(raw).strip():
        return None
    try:
        return float(raw)
    except ValueError:
        return None


def load_budget(
    path: Path | None = None,
    environ: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Presupuesto efectivo: fichero, perfil de CI y overrides por entorno.

    Precedencia: valores base < ``[ci.*]`` (si ``CI``/``TAIKI_CI``) <
    ``TAIKI_SUITE_BUDGET`` / ``TAIKI_MODULE_BUDGET``. Así el gate local sigue
    estricto y el runner compartido de CI tiene margen sin tocar el fichero.
    """
    data = load_toml(path or CONFIG / "budget.toml")
    env = os.environ if environ is None else environ
    budget = {
        "suite": dict(DEFAULT_BUDGET["suite"]),
        "module": dict(DEFAULT_BUDGET["module"]),
    }
    for section in ("suite", "module"):
        budget[section].update(data.get(section, {}))
    if _truthy(env.get("CI")) or _truthy(env.get("TAIKI_CI")):
        ci = data.get("ci", {})
        for section in ("suite", "module"):
            budget[section].update(ci.get(section, {}))
    for name, section in (
        ("TAIKI_SUITE_BUDGET", "suite"),
        ("TAIKI_MODULE_BUDGET", "module"),
    ):
        override = _float_env(env, name)
        if override is not None:
            budget[section]["max_seconds"] = override
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


def parse_source(path: Path) -> tuple[str, ast.Module]:
    """Texto y AST del fichero, reutilizados mientras no cambie en disco."""
    path = Path(path)
    stat = path.stat()
    stamp = (stat.st_mtime_ns, stat.st_size)
    cached = _PARSE_CACHE.get(path)
    if cached is not None and cached[0] == stamp:
        return cached[1], cached[2]
    text = path.read_text(encoding="utf-8")
    tree = ast.parse(text)
    _PARSE_CACHE[path] = (stamp, text, tree)
    return text, tree


def _walk(tree: ast.AST) -> list[ast.AST]:
    return list(ast.walk(tree))


def _unused_imports(nodes: list[ast.AST]) -> list[str]:
    imported: dict[str, int] = {}
    used: set[str] = set()
    for node in nodes:
        if isinstance(node, ast.Import):
            for alias in node.names:
                imported[(alias.asname or alias.name).split(".")[0]] = node.lineno
        elif isinstance(node, ast.ImportFrom):
            for alias in node.names:
                if alias.name != "*":
                    imported[alias.asname or alias.name] = node.lineno
        elif isinstance(node, ast.Name):
            used.add(node.id)
    return sorted(
        name
        for name in imported
        if name not in used and name != "annotations"
    )


def unused_imports(path: Path, tree: ast.AST | None = None) -> list[str]:
    """Nombres importados que el módulo no usa."""
    module = tree if tree is not None else parse_source(path)[1]
    return _unused_imports(_walk(module))


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


def _unsafe_skips(nodes: list[ast.AST]) -> list[str]:
    found: list[str] = []
    skip_names = {"skip", "skipIf", "skipUnless", "xfail"}
    for node in nodes:
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


def unsafe_skips(tree: ast.AST) -> list[str]:
    """Skip/xfail sin motivo: decoradores o llamadas sueltas."""
    return _unsafe_skips(_walk(tree))


def scan_source(name: str, path: Path | None = None) -> ModuleSource:
    """Cuenta comprobaciones fuente, líneas, imports muertos y mensajes."""
    target = path or CONFIG / f"{name}.py"
    text, tree = parse_source(target)
    nodes = _walk(tree)
    source = ModuleSource(name=name, lines=len(text.splitlines()))
    source.unused_imports = _unused_imports(nodes)
    source.unsafe_skips = _unsafe_skips(nodes)
    for node in nodes:
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
    tree = parse_source(target)[1]
    return [
        node.name
        for node in tree.body
        if isinstance(node, ast.FunctionDef) and not node.name.startswith("_")
    ]


def used_names(paths: Iterable[Path]) -> set[str]:
    used: set[str] = set()
    for path in paths:
        for node in ast.walk(parse_source(Path(path))[1]):
            if isinstance(node, ast.Name):
                used.add(node.id)
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
        f"Techo: {baseline} (+{tolerance}) · real: {total} ({delta:+d})"
    )
    if total > baseline + tolerance:
        lines.append("  Supera el techo: poda tests o justifica el aumento.")
    return lines


def budget_violations(
    results: list[dict[str, Any]],
    budget: dict[str, Any],
    partial: bool = False,
) -> list[str]:
    """Incumplimientos duros de tiempo (suite y por módulo).

    Es la fuente única de los mensajes de tiempo: ``budget_warnings`` los usa
    para avisar y ``run.py --strict-budget`` para fallar en CI.
    """
    violations: list[str] = []
    suite = budget["suite"]
    module_limit = float(budget["module"].get("max_seconds", 0))
    total_time = sum(row.get("elapsed", 0.0) for row in results)
    max_suite = float(suite.get("max_seconds", 0))
    if not partial and max_suite and total_time > max_suite:
        violations.append(
            f"suite en {total_time:.2f}s supera el presupuesto de {max_suite:.2f}s"
        )
    if module_limit:
        for row in results:
            if row.get("elapsed", 0.0) > module_limit:
                violations.append(
                    f"{row['name']} tarda {row['elapsed']:.2f}s "
                    f"(máximo {module_limit:.2f}s)"
                )
    return violations


def check_deviation(
    results: list[dict[str, Any]],
    budget: dict[str, Any],
    partial: bool = False,
) -> str | None:
    """Desviación del techo de comprobaciones, o ``None`` si está dentro.

    El techo es global: se compara el total de la suite, no cada módulo. Es un
    techo, no un objetivo: bajar comprobaciones es válido (poda) mientras la
    cobertura se mantenga; solo preocupa pasarse del margen por arriba.
    """
    if partial:
        return None
    suite = budget["suite"]
    total_checks = sum(row.get("checks", 0) for row in results)
    baseline = int(suite.get("baseline_checks", 0) or 0)
    tolerance = int(suite.get("checks_tolerance", 0) or 0)
    if baseline and total_checks > baseline + tolerance:
        return (
            f"{total_checks} comprobaciones superan el techo de {baseline} "
            f"(+{tolerance}); poda o justifica el aumento"
        )
    return None


def budget_warnings(
    results: list[dict[str, Any]],
    budget: dict[str, Any],
    partial: bool = False,
) -> list[str]:
    """Avisos accionables por tiempo o desviación de la línea base."""
    warnings = budget_violations(results, budget, partial=partial)
    deviation = check_deviation(results, budget, partial=partial)
    if deviation:
        warnings.append(deviation)
    return warnings


def budget_failures(
    results: list[dict[str, Any]],
    budget: dict[str, Any],
    partial: bool = False,
) -> list[str]:
    """Fallos duros de ``--strict-budget``: tiempo y techo de comprobaciones.

    El merge y la CI usan este gate para que la acumulación de checks falle en
    el punto de integración en vez de descubrirse en el siguiente ticket.
    """
    failures = budget_violations(results, budget, partial=partial)
    deviation = check_deviation(results, budget, partial=partial)
    if deviation:
        failures.append(deviation)
    return failures
