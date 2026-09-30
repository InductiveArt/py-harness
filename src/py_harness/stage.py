import os
import subprocess
import sys
import tempfile
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import TypeAlias

from py_harness.console import err
from py_harness.console import out
from py_harness.layout import source_root
from py_harness.layout import suite_paths
from py_harness.layout import suite_patterns
from py_harness.pytest_plugin import INTEGRATION
from py_harness.units import UnitsError
from py_harness.units import normalized_name
from py_harness.units import project_name
from py_harness.units import resolve_units

# Each test file is imported by its path, so two in different folders may share a name.
PYTEST = (
    "pytest",
    "--strict-markers",
    "--strict-config",
    "-o",
    "xfail_strict=true",
    "--import-mode=importlib",
)
# Arguments pytest takes from its caller's environment; only the harness decides which tests run.
CALLER_ARGUMENTS = "PYTEST_ADDOPTS"
NOTHING_COLLECTED = 5


@dataclass(frozen=True)
class Run:
    command: list[str]
    # Why collecting no test is a pass here; without one it is a failure.
    when_empty: str | None = None


@dataclass(frozen=True)
class Skip:
    reason: str


@dataclass(frozen=True)
class Refuse:
    reason: str


Step: TypeAlias = Run | Skip | Refuse


def main(argv: list[str]) -> int:
    harness, stage = Path(argv[1]), argv[2]
    plan = STAGES.get(stage)
    if plan is None:
        err(f"unknown stage: {stage} (expected {', '.join(STAGES)})")
        return 2
    try:
        units = resolve_units(Path.cwd())
    except UnitsError as error:
        err(f"units: {error}")
        return 1
    if len(argv) > 3:
        chosen = unit_named(units, argv[3])
        if chosen is None:
            return 2
        units = [chosen]
    with tempfile.TemporaryDirectory(prefix="py-harness-") as scratch:
        # Coverage writes a data file per process. A directory private to this
        # run keeps them out of the repository and apart from any run nested in it.
        inherited = {key: value for key, value in os.environ.items() if key != CALLER_ARGUMENTS}
        environment = {**inherited, "COVERAGE_FILE": str(Path(scratch) / "coverage")}
        for unit in units:
            if perform(stage, unit, plan(unit, harness), environment) != 0:
                return 1
    return 0


def perform(stage: str, unit: Path, step: Step, environment: dict[str, str]) -> int:
    out()
    if isinstance(step, Skip):
        out(f"·  skip {stage} {unit.as_posix()} ({step.reason})")
        return 0
    out(f"→ {stage} {unit.as_posix()}")
    if isinstance(step, Refuse):
        err(f"{stage}: {unit.as_posix()} {step.reason}")
        return 1
    code = subprocess.run(step.command, env=environment, check=False).returncode
    if code == NOTHING_COLLECTED and step.when_empty is not None:
        out(f"·  skip {stage} {unit.as_posix()} ({step.when_empty})")
        return 0
    return code


def unit_named(units: list[Path], name: str) -> Path | None:
    for unit in units:
        if project_name(unit) == normalized_name(name):
            return unit
    out(f"unknown package: {name}")
    out("available:")
    for unit in units:
        out(f"  {project_name(unit)}  ({unit.as_posix()})")
    return None


# #region Stages


def plan_test(unit: Path, _harness: Path) -> Step:
    return plan_suite(unit, ["-m", f"not {INTEGRATION}"], f"every test is marked {INTEGRATION}")


def plan_test_integration(unit: Path, _harness: Path) -> Step:
    return plan_suite(unit, ["-m", INTEGRATION], f"no test is marked {INTEGRATION}")


def plan_suite(unit: Path, selection: list[str], when_empty: str) -> Step:
    suite = suite_paths(unit)
    if not suite:
        return Skip(nothing_at(unit))
    return Run([*PYTEST, *posix(suite), *selection], when_empty=when_empty)


def plan_coverage(unit: Path, harness: Path) -> Step:
    """Every test of the unit, measured against the unit's own source only."""
    source, suite = source_root(unit), suite_paths(unit)
    if not source.is_dir():
        return Skip(f"no {source.as_posix()}")
    if not suite:
        return Refuse(f"has {source.as_posix()} and {nothing_at(unit)}")
    return Run(
        [
            *PYTEST,
            *posix(suite),
            f"--cov={source.as_posix()}",
            f"--cov-config={(harness / 'coverage.toml').as_posix()}",
            "--cov-report=term-missing:skip-covered",
        ]
    )


def plan_typecheck(unit: Path, _harness: Path) -> Step:
    return Run(["basedpyright", unit.as_posix()])


def nothing_at(unit: Path) -> str:
    return f"no tests at {', '.join(suite_patterns(unit))}"


def posix(paths: list[Path]) -> list[str]:
    return [path.as_posix() for path in paths]


STAGES: dict[str, Callable[[Path, Path], Step]] = {
    "test": plan_test,
    "test-integration": plan_test_integration,
    "coverage": plan_coverage,
    "typecheck": plan_typecheck,
}


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
