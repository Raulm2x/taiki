"""Pruebas de la propia auditoría: presupuesto, cuarentena y ruido."""

from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path
from tests import hygiene
from tests.support import Harness


def run(h: Harness) -> None:
    tmp = Path(h.tmp)
    sample = tmp / "test_sample.py"
    sample.write_text(
        "import os\n"
        "from json import dumps\n"
        "\n"
        "def run(h):\n"
        "    h.ok(True, 'uno')\n"
        "    h.raises(ValueError, lambda: None, 'dos')\n",
        encoding="utf-8",
    )
    source = hygiene.scan_source("test_sample", sample)
    h.eq(source.checks, 2, "scan_source cuenta ok y raises")
    h.eq(source.unused_imports, ["dumps", "os"], "scan_source detecta imports muertos")

    skip_sample = tmp / "test_skips.py"
    skip_sample.write_text(
        "@pytest.mark.skip\n"
        "def test_sin_motivo():\n"
        "    pass\n",
        encoding="utf-8",
    )
    h.eq(
        hygiene.scan_source("test_skips", skip_sample).unsafe_skips,
        ["test_sin_motivo"],
        "scan_source detecta skip sin motivo",
    )

    first = hygiene.ModuleSource("a", messages=[("repetido", 1)])
    second = hygiene.ModuleSource("b", messages=[("repetido", 2)])
    h.eq(
        hygiene.duplicate_messages([first, second]),
        {"repetido": ["a", "b"]},
        "duplicate_messages cruza módulos",
    )
    h.eq(
        hygiene.duplicate_messages([first]),
        {},
        "un mensaje único no es duplicado",
    )

    today = date.today()
    valid = {
        "reason": "fallo conocido",
        "owner": "opencode",
        "expires": (today + timedelta(days=30)).isoformat(),
    }
    h.eq(
        hygiene.quarantine_problem("test_x", valid, today=today),
        None,
        "una cuarentena vigente es válida",
    )
    expired = {**valid, "expires": (today - timedelta(days=1)).isoformat()}
    h.ok(
        "caducada" in (hygiene.quarantine_problem("test_x", expired, today=today) or ""),
        "una cuarentena caducada se detecta",
    )
    h.ok(
        "owner"
        in (
            hygiene.quarantine_problem(
                "test_x", {"reason": "x", "expires": valid["expires"]}
            )
            or ""
        ),
        "una cuarentena incompleta nombra el campo que falta",
    )
    h.ok(
        "inválida"
        in (
            hygiene.quarantine_problem(
                "test_x", {**valid, "expires": "31/12/2026"}, today=today
            )
            or ""
        ),
        "una fecha inválida se reporta",
    )

    budget_file = tmp / "budget.toml"
    budget_file.write_text(
        "[suite]\nmax_seconds = 1.0\nbaseline_checks = 10\nchecks_tolerance = 2\n"
        "[module]\nmax_seconds = 0.25\n",
        encoding="utf-8",
    )
    budget = hygiene.load_budget(budget_file)
    h.eq(budget["suite"]["max_seconds"], 1.0, "load_budget lee el fichero")
    h.eq(
        hygiene.load_budget(tmp / "no-existe.toml")["suite"]["max_seconds"],
        2.0,
        "load_budget cae a los valores por defecto",
    )

    results = [{"name": "test_x", "checks": 40, "elapsed": 1.5, "error": None}]
    warnings = hygiene.budget_warnings(results, budget)
    h.ok(
        any("presupuesto" in item for item in warnings),
        "budget_warnings avisa del exceso de tiempo",
    )
    h.ok(
        any("techo" in item for item in warnings),
        "budget_warnings avisa al superar el techo de comprobaciones",
    )
    low = [{"name": "test_x", "checks": 2, "elapsed": 0.01, "error": None}]
    h.ok(
        not any("techo" in item for item in hygiene.budget_warnings(low, budget)),
        "bajar comprobaciones no genera aviso: el presupuesto es un techo",
    )
    partial = hygiene.budget_warnings(results, budget, partial=True)
    h.ok(
        all("presupuesto" not in item and "techo" not in item for item in partial)
        and any("test_x" in item for item in partial),
        "una ejecución parcial solo avisa del presupuesto por módulo",
    )

    # ── El presupuesto de tiempo también es un gate duro ────────────────
    h.eq(
        len(hygiene.budget_violations(results, budget)),
        2,
        "budget_violations devuelve suite y módulo fuera de presupuesto",
    )
    fast = [{"name": "test_x", "checks": 10, "elapsed": 0.01, "error": None}]
    h.eq(
        hygiene.budget_violations(fast, budget),
        [],
        "budget_violations no inventa incumplimientos dentro del presupuesto",
    )

    # ── Umbral de cobertura por capa ────────────────────────────────────
    from tests import coverage_gate

    fake_files = {
        "ticketlib/a.py": {"summary": {"covered_lines": 90, "num_statements": 100}},
        "ticketlib/b.py": {"summary": {"covered_lines": 10, "num_statements": 20}},
        "tui/app.py": {"summary": {"covered_lines": 0, "num_statements": 500}},
        "tui/state.py": {"summary": {"covered_lines": 8, "num_statements": 10}},
        "commands/cli.py": {"summary": {"covered_lines": 1, "num_statements": 10}},
    }
    totals = coverage_gate.layer_totals(fake_files, ["tui/app.py"])
    h.eq(totals["ticketlib"], (100, 120), "layer_totals suma por capa")
    h.eq(totals["tui"], (8, 10), "layer_totals respeta los ficheros ignorados")
    h.eq(
        coverage_gate.evaluate(
            totals, {"ticketlib": 80, "mcp_layer": 80, "commands": 5}
        ),
        ["mcp_layer: sin datos de cobertura (mínimo 80%)"],
        "evaluate falla si falta una capa y no si supera el mínimo",
    )
    h.ok(
        coverage_gate.evaluate(totals, {"commands": 35}),
        "evaluate detecta una capa por debajo del umbral",
    )
    thresholds, ignore = coverage_gate.load_thresholds()
    h.ok(
        {"ticketlib", "mcp_layer", "commands", "tui"} <= set(thresholds)
        and "tui/app.py" in ignore,
        "los umbrales por capa están en pyproject.toml",
    )

    # ── --strict-budget convierte el tiempo en un gate duro ─────────────
    import contextlib
    import io

    from tests import run as runner

    original_runner = runner._run_module
    original_budget_loader = hygiene.load_budget
    tight = {
        "suite": {"max_seconds": 1.0, "baseline_checks": 0, "checks_tolerance": 20},
        "module": {"max_seconds": 0.1},
    }
    runner._run_module = lambda name: {
        "name": name,
        "checks": 1,
        "error": None,
        "elapsed": 99.0,
    }
    hygiene.load_budget = lambda path=None: tight
    try:
        with contextlib.redirect_stdout(io.StringIO()):
            strict_code = runner.main(["test_core", "--strict-budget"])
            loose_code = runner.main(["test_core"])
        h.eq(strict_code, 1, "--strict-budget falla con un módulo lento")
        h.eq(loose_code, 0, "sin --strict-budget el tiempo solo avisa")
    finally:
        runner._run_module = original_runner
        hygiene.load_budget = original_budget_loader

    quarantine_file = tmp / "quarantine.toml"
    quarantine_file.write_text(
        "[modules.test_x]\n"
        'reason = "motivo"\n'
        'owner = "opencode"\n'
        f'expires = "{(today + timedelta(days=7)).isoformat()}"\n',
        encoding="utf-8",
    )
    loaded = hygiene.load_quarantine(quarantine_file)
    h.eq(loaded["test_x"]["owner"], "opencode", "load_quarantine lee la entrada")

    names = sorted(path.stem for path in Path(__file__).parent.glob("test_*.py"))
    report = hygiene.audit(names)
    dead_imports = [item.name for item in report["sources"] if item.unused_imports]
    h.eq(dead_imports, [], "la suite no tiene imports muertos")
    h.eq(report["duplicates"], {}, "la suite no tiene mensajes duplicados")
    h.eq(report["unused_seeds"], [], "todos los seeds se usan")

    import importlib

    discovered = hygiene.discover_modules()
    h.eq(
        set(discovered),
        {path.stem for path in Path(__file__).parent.glob("test_*.py")},
        "el descubrimiento incluye todos los test_*.py",
    )
    for name in discovered:
        module = importlib.import_module(f"tests.{name}")
        h.ok(
            callable(getattr(module, "run", None)),
            f"{name} define run y entra en la suite",
        )
