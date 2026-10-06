import os
import subprocess
import sys
import tempfile
from collections.abc import Callable
from dataclasses import dataclass
from dataclasses import field
from pathlib import Path
from typing import TypeAlias

from py_harness.console import err
from py_harness.console import out
from py_harness.findings import FIRST
from py_harness.findings import basedpyright_errors
from py_harness.findings import by_file
from py_harness.findings import counted
from py_harness.findings import coverage_gaps
from py_harness.findings import failed_tests
from py_harness.findings import ruff_findings
from py_harness.findings import unformatted
from py_harness.layout import source_root
from py_harness.layout import suite_paths
from py_harness.layout import suite_patterns
from py_harness.pytest_plugin import INTEGRATION
from py_harness.pytest_plugin import REPORT_VARIABLE
from py_harness.units import UnitsError
from py_harness.units import normalized_name
from py_harness.units import project_name
from py_harness.units import resolve_units
from py_harness.verdict import BROKEN
from py_harness.verdict import FAILED
from py_harness.verdict import PASSED
from py_harness.verdict import SKIPPED
from py_harness.verdict import Section
from py_harness.verdict import Verdict
from py_harness.verdict import report
from py_harness.wiring import wiring_problems

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
# What ruff and basedpyright mean by an exit code: nothing found, or findings in their report.
# Any other code, or a report that cannot be read, means the checker itself broke.
CLEAN = 0
FOUND = 1
TEST_REPORT = "tests.json"
COVERAGE_REPORT = "coverage.json"


@dataclass(frozen=True)
class Run:
    command: list[str]
    # Why collecting no test is a pass here; without one it is a failure.
    when_empty: str | None = None
    # The checker writes its report to standard output, so the stage keeps it to read.
    reads_output: bool = False
    # The source whose share the tests run is measured, when the run measures one.
    measures: Path | None = None


@dataclass(frozen=True)
class Skip:
    reason: str


@dataclass(frozen=True)
class Refuse:
    reason: str


Step: TypeAlias = Run | Skip | Refuse


@dataclass(frozen=True)
class Ran:
    """A finished command: its exit code, its output when the stage keeps it, and its reports."""

    code: int
    stdout: str
    reports: Path
    measured: Path | None


@dataclass(frozen=True)
class Share:
    """How much of a source the tests run: one unit's source, or several units together."""

    percent: float
    of: str


@dataclass(frozen=True)
class Tested:
    passed: int
    failed: int
    # None when the session measured nothing, or stopped before it could.
    share: Share | None

    def headline(self) -> str:
        counts = f"{self.passed} passed, {self.failed} failed"
        if self.share is None:
            return counts
        # Floored, so a run short of full coverage never reads as 100%.
        percent = f"{int(self.share.percent * 10) / 10:g}%"
        return f"{counts}, {percent} of {self.share.of}"


@dataclass(frozen=True)
class Judged:
    verdict: Verdict
    # What a person running the stage alone reads, one finding per line.
    shown: list[str] = field(default_factory=list[str])
    # What a passing test session ran, so the sessions of several units add up to one line.
    tested: Tested | None = None


@dataclass(frozen=True)
class Stage:
    # Plans the run of one unit, or of the whole repository when the unit is None.
    plan: Callable[[Path | None, Path, Path], Step]
    judge: Callable[[Ran, Path], Judged]
    whole_repository: bool = False
    # Runs only once every tool config reaches the harness's.
    wired: bool = False


def main(argv: list[str]) -> int:
    harness, name = Path(argv[1]), argv[2]
    stage = STAGES.get(name)
    if stage is None:
        err(f"unknown stage: {name} (expected {', '.join(STAGES)})")
        return 2
    root = Path.cwd()
    if stage.wired and (problems := wiring_problems(root, harness)):
        for problem in problems:
            err(f"wiring: {problem}")
        report(Verdict(SKIPPED, "not run: a tool config does not reach the harness's (see wiring)"))
        return 1
    package = argv[3] if len(argv) > 3 else None
    try:
        targets = chosen(stage, package, root)
    except UnitsError as error:
        err(f"units: {error}")
        report(Verdict(FAILED, f"units: {error}"))
        return 1
    if targets is None:
        report(Verdict(FAILED, f"unknown package: {package}"))
        return 2
    verdict = performed(name, stage, harness, targets)
    report(verdict)
    return 0 if verdict.outcome == PASSED else 1


def chosen(stage: Stage, package: str | None, root: Path) -> list[Path | None] | None:
    """The whole repository once, or each unit, or the one unit named; None when none is."""
    if stage.whole_repository and package is None:
        return [None]
    units = resolve_units(root)
    if package is None:
        return list(units)
    unit = unit_named(units, package)
    return None if unit is None else [unit]


def performed(name: str, stage: Stage, harness: Path, targets: list[Path | None]) -> Verdict:
    """Runs each target in turn and stops at the first that does not pass."""
    with tempfile.TemporaryDirectory(prefix="py-harness-") as scratch:
        # Coverage writes a data file per process. A directory private to this
        # run keeps them out of the repository and apart from any run nested in it.
        inherited = {key: value for key, value in os.environ.items() if key != CALLER_ARGUMENTS}
        environment = {**inherited, "COVERAGE_FILE": str(Path(scratch) / "coverage")}
        passed: list[Judged] = []
        for index, target in enumerate(targets):
            reports = Path(scratch) / str(index)
            reports.mkdir()
            judged = perform(name, target, stage, harness, reports, environment)
            if judged.verdict.outcome != PASSED:
                left = len(targets) - index - 1
                return stopped(judged.verdict, left) if left else judged.verdict
            passed.append(judged)
    return together(passed)


def together(passed: list[Judged]) -> Verdict:
    """One target speaks for itself; several add up the tests each one ran."""
    if len(passed) == 1:
        return passed[0].verdict
    tested = [judged.tested for judged in passed if judged.tested is not None]
    shares = [each.share for each in tested if each.share is not None]
    # Each unit is held to its own share, so together they reach the least of them.
    least = min((share.percent for share in shares), default=None)
    share = None if least is None else Share(least, counted(len(shares), "unit"))
    total = Tested(sum(each.passed for each in tested), sum(each.failed for each in tested), share)
    return Verdict(PASSED, total.headline())


def stopped(verdict: Verdict, left: int) -> Verdict:
    headline = f"{verdict.headline}; {counted(left, 'unit')} after it not run"
    return Verdict(verdict.outcome, headline, verdict.counts, verdict.first, verdict.sections)


def perform(
    name: str,
    unit: Path | None,
    stage: Stage,
    harness: Path,
    reports: Path,
    environment: dict[str, str],
) -> Judged:
    label = name if unit is None else f"{name} {unit.as_posix()}"
    step = stage.plan(unit, harness, reports)
    out()
    if isinstance(step, Skip):
        out(f"·  skip {label} ({step.reason})")
        return Judged(Verdict(PASSED, f"skipped {label}: {step.reason}"))
    out(f"→ {label}")
    if isinstance(step, Refuse):
        refused = f"{(unit or Path()).as_posix()} {step.reason}"
        err(f"{name}: {refused}")
        return Judged(Verdict(FAILED, refused))
    try:
        completed = subprocess.run(
            step.command,
            env={**environment, REPORT_VARIABLE: str(reports / TEST_REPORT)},
            stdout=subprocess.PIPE if step.reads_output else None,
            text=True,
            check=False,
        )
    except OSError as error:
        err(f"{label}: could not start {step.command[0]}: {error.strerror}")
        return Judged(Verdict(BROKEN, f"could not start {step.command[0]}: {error.strerror}"))
    if completed.returncode == NOTHING_COLLECTED and step.when_empty is not None:
        out(f"·  skip {label} ({step.when_empty})")
        return Judged(Verdict(PASSED, f"skipped {label}: {step.when_empty}"))
    ran = Ran(completed.returncode, completed.stdout or "", reports, step.measures)
    judged = stage.judge(ran, Path.cwd())
    for line in judged.shown:
        out(line)
    return judged


def unit_named(units: list[Path], name: str) -> Path | None:
    for unit in units:
        if project_name(unit) == normalized_name(name):
            return unit
    out(f"unknown package: {name}")
    out("available:")
    for unit in units:
        out(f"  {project_name(unit)}  ({unit.as_posix()})")
    return None


# #region Judging: the exit code says whether a checker found something; its report says what.


def judge_fix(ran: Ran, _root: Path) -> Judged:
    """A fix applies what it can and passes; a fix that cannot run has broken."""
    return Judged(Verdict(PASSED)) if ran.code == CLEAN else broke("ruff", ran)


def judge_format(ran: Ran, root: Path) -> Judged:
    files = unformatted(ran.stdout, root) if ran.code in (CLEAN, FOUND) else None
    if files is None or (ran.code == FOUND) != bool(files):
        return broke("ruff", ran)
    if not files:
        return Judged(Verdict(PASSED))
    headline = f"{counted(len(files), 'file')} to reformat"
    listed = [Section("Files to reformat", files)]
    verdict = Verdict(FAILED, headline, first=files[:FIRST], sections=listed)
    return Judged(verdict, [f"{path}: would be reformatted" for path in files])


def judge_lint(ran: Ran, root: Path) -> Judged:
    found = ruff_findings(ran.stdout, root) if ran.code in (CLEAN, FOUND) else None
    if found is None or (ran.code == FOUND) != bool(found):
        return broke("ruff", ran)
    if not found:
        return Judged(Verdict(PASSED))
    return Judged(by_file(found, "finding"), [finding.text() for finding in found])


def judge_typecheck(ran: Ran, root: Path) -> Judged:
    found = basedpyright_errors(ran.stdout, root) if ran.code in (CLEAN, FOUND) else None
    if found is None or (ran.code == FOUND) != bool(found):
        return broke("basedpyright", ran)
    if not found:
        return Judged(Verdict(PASSED))
    shown = [line for finding in found for line in finding.shown()]
    return Judged(by_file(found, "error"), shown)


def judge_tests(ran: Ran, root: Path) -> Judged:
    """Judged on what the tests did: any test that failed, then any code no test runs.

    A pass needs proof: the session's record, a session that ran to its end, and, when the
    run measured its source, the measure. Without them what the tests did is unknown.
    """
    recorded = failed_tests(ran.reports / TEST_REPORT, root)
    if recorded is None:
        return broke("pytest", ran)
    passed, problems = recorded
    share, gaps = measure(ran, root)
    tested = Tested(passed, len(problems), share)
    if problems:
        first = [f"{problem.test}: {problem.message}" for problem in problems[:FIRST]]
        sections = [Section(problem.test, problem.text.splitlines()) for problem in problems]
        return Judged(Verdict(FAILED, tested.headline(), first=first, sections=sections))
    if gaps:
        first = [f"{gap.title}: {'; '.join(gap.lines)}" for gap in gaps[:FIRST]]
        return Judged(Verdict(FAILED, tested.headline(), first=first, sections=gaps))
    if ran.code != CLEAN or (ran.measured is not None and share is None):
        return broke("pytest", ran)
    return Judged(Verdict(PASSED, tested.headline()), tested=tested)


def measure(ran: Ran, root: Path) -> tuple[Share | None, list[Section]]:
    """The share of its source the run reached, and each file's code no test runs."""
    if ran.measured is None:
        return None, []
    # A session stopped while collecting leaves no coverage report: it measured nothing.
    gauged = coverage_gaps(ran.reports / COVERAGE_REPORT, root)
    if gauged is None:
        return None, []
    percent, gaps = gauged
    return Share(percent, ran.measured.as_posix()), gaps


def broke(tool: str, ran: Ran) -> Judged:
    """The checker did not do what it promises, so its findings are unknown; its output is kept."""
    how = " and its report could not be read" if ran.code in (CLEAN, FOUND) else ""
    return Judged(Verdict(BROKEN, f"{tool} exited {ran.code}{how}"), ran.stdout.splitlines())


# #region Plans


def plan_format_fix(_unit: Path | None, _harness: Path, _reports: Path) -> Step:
    return Run(["ruff", "format", "."])


def plan_lint_fix(_unit: Path | None, _harness: Path, _reports: Path) -> Step:
    return Run(["ruff", "check", "--fix-only", "."])


def plan_lint_fix_unsafe(_unit: Path | None, _harness: Path, _reports: Path) -> Step:
    return Run(["ruff", "check", "--fix-only", "--unsafe-fixes", "."])


def plan_format(_unit: Path | None, _harness: Path, _reports: Path) -> Step:
    return Run(["ruff", "format", "--check", "--output-format", "json", "."], reads_output=True)


def plan_lint(_unit: Path | None, _harness: Path, _reports: Path) -> Step:
    return Run(["ruff", "check", "--output-format", "json", "."], reads_output=True)


def plan_typecheck(unit: Path | None, _harness: Path, _reports: Path) -> Step:
    within = [] if unit is None else [unit.as_posix()]
    return Run(["basedpyright", "--outputjson", *within], reads_output=True)


def plan_test(unit: Path | None, _harness: Path, _reports: Path) -> Step:
    return plan_suite(unit, ["-m", f"not {INTEGRATION}"], f"every test is marked {INTEGRATION}")


def plan_test_integration(unit: Path | None, _harness: Path, _reports: Path) -> Step:
    return plan_suite(unit, ["-m", INTEGRATION], f"no test is marked {INTEGRATION}")


def plan_suite(unit: Path | None, selection: list[str], when_empty: str) -> Step:
    place = unit or Path()
    suite = suite_paths(place)
    if not suite:
        return Skip(nothing_at(place))
    return Run([*PYTEST, *posix(suite), *selection], when_empty=when_empty)


def plan_coverage(unit: Path | None, harness: Path, reports: Path) -> Step:
    """Every test of the unit, measured against the unit's own source only."""
    place = unit or Path()
    source, suite = source_root(place), suite_paths(place)
    if not source.is_dir():
        return Skip(f"no {source.as_posix()}")
    if not suite:
        return Refuse(f"has {source.as_posix()} and {nothing_at(place)}")
    return Run(
        [
            *PYTEST,
            *posix(suite),
            f"--cov={source.as_posix()}",
            f"--cov-config={(harness / 'coverage.toml').as_posix()}",
            "--cov-report=term-missing:skip-covered",
            f"--cov-report=json:{(reports / COVERAGE_REPORT).as_posix()}",
        ],
        measures=source,
    )


def nothing_at(unit: Path) -> str:
    return f"no tests at {', '.join(suite_patterns(unit))}"


def posix(paths: list[Path]) -> list[str]:
    return [path.as_posix() for path in paths]


STAGES: dict[str, Stage] = {
    "format-fix": Stage(plan_format_fix, judge_fix, whole_repository=True, wired=True),
    "lint-fix": Stage(plan_lint_fix, judge_fix, whole_repository=True, wired=True),
    "lint-fix-unsafe": Stage(plan_lint_fix_unsafe, judge_fix, whole_repository=True, wired=True),
    "format": Stage(plan_format, judge_format, whole_repository=True, wired=True),
    "lint": Stage(plan_lint, judge_lint, whole_repository=True, wired=True),
    "typecheck": Stage(plan_typecheck, judge_typecheck, whole_repository=True, wired=True),
    "test": Stage(plan_test, judge_tests),
    "test-integration": Stage(plan_test_integration, judge_tests),
    "coverage": Stage(plan_coverage, judge_tests, wired=True),
}


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
