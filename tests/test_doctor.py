import sys
from pathlib import Path

import pytest

from py_harness import guard
from py_harness.doctor import Checked
from py_harness.doctor import judged
from py_harness.guard import CRASHED
from py_harness.verdict import BROKEN
from py_harness.verdict import FAILED
from py_harness.verdict import PASSED
from py_harness.verdict import Part
from tests.support import SHARE
from tests.support import Project

PRINTS_UNITS = 'import os\n\nprint(os.environ["PY_HARNESS_UNITS"])  # noqa: T201\n'


def test_doctor_runs_every_shared_rule(project: Project) -> None:
    result = project.make("doctor")
    assert result.returncode == 0
    verdicts = ("boundaries skipped", "no-comment-overreach OK", "no-cycles OK", "no-reexport OK")
    for verdict in (
        *verdicts,
        "no-blanket-exemptions OK",
        "no-hidden-names OK",
        "no-secrets OK",
        "suppressions none",
        "shared-names none",
    ):
        assert f"doctor: {verdict}" in result.stdout


def test_a_projects_own_checks_run_beside_the_shared_ones(project: Project) -> None:
    project.write(".py-harness/doctor/rule-local.py", 'print("local ran")  # noqa: T201\n')
    assert "local ran" in project.make("doctor").stdout


def test_rules_run_first_then_drifts_then_reports(project: Project) -> None:
    project.write(".py-harness/doctor/report-local.py", 'print("local report")  # noqa: T201\n')
    project.write(".py-harness/doctor/rule-local.py", 'print("local rule")  # noqa: T201\n')
    project.write(".py-harness/doctor/drift-local.py", 'print("local drift")  # noqa: T201\n')
    stdout = project.make("doctor").stdout
    expected = (
        "doctor: no-secrets OK",
        "local rule",
        "local drift",
        "doctor: suppressions none",
        "local report",
    )
    positions = [stdout.index(line) for line in expected]
    assert positions == sorted(positions)


def test_one_line_verdicts_stay_together_and_longer_output_is_set_off(project: Project) -> None:
    project.write(".py-harness/doctor/rule-a.py", 'print("a1\\na2")  # noqa: T201\n')
    project.write(".py-harness/doctor/rule-b.py", 'print("b1\\nb2")  # noqa: T201\n')
    project.write(".py-harness/doctor/report-z.py", 'print("z1\\nz2")  # noqa: T201\n')
    stdout = project.make("doctor").stdout
    verdicts_then_blocks = "doctor: no-reexport OK\ndoctor: no-secrets OK\n\na1\na2\n\nb1\nb2\n\n"
    assert verdicts_then_blocks + "doctor: shared-names none\n" in stdout
    assert stdout.endswith("doctor: suppressions none\n\nz1\nz2\n")


def test_a_file_without_a_kind_prefix_is_not_run(project: Project) -> None:
    project.write(".py-harness/doctor/helper.py", 'print("helper ran")  # noqa: T201\n')
    assert "helper ran" not in project.make("doctor").stdout


def test_a_failing_check_fails_the_doctor_after_every_check_ran(project: Project) -> None:
    project.write(".py-harness/doctor/rule-a.py", "raise SystemExit(1)\n")
    project.write(".py-harness/doctor/rule-b.py", 'print("b ran")  # noqa: T201\n')
    result = project.make("doctor")
    assert result.returncode != 0
    assert "b ran" in result.stdout


def test_rule_selects_a_check_by_its_short_name(project: Project) -> None:
    result = project.make("doctor", "RULE=no-cycles")
    assert "doctor: no-cycles OK" in result.stdout
    assert "no-reexport" not in result.stdout


def test_rule_selects_a_check_by_its_full_name(project: Project) -> None:
    result = project.make("doctor", "RULE=rule-no-cycles")
    assert "doctor: no-cycles OK" in result.stdout
    assert "no-reexport" not in result.stdout


def test_an_unmatched_rule_fails(project: Project) -> None:
    result = project.make("doctor", "RULE=nope")
    assert result.returncode != 0
    assert "doctor: no check matched 'nope'" in result.stdout


def test_checks_receive_the_units_resolved_once(project: Project) -> None:
    project.write(".py-harness/doctor/report-units.py", PRINTS_UNITS)
    project.write(".py-harness/doctor/report-again.py", PRINTS_UNITS)
    workspace = '\n[tool.uv.workspace]\nmembers = ["libs/*"]\n'
    project.write("pyproject.toml", project.read("pyproject.toml") + workspace)
    project.write("libs/old/pyproject.toml", '[project]\nname = "old"\nversion = "0.0.0"\n')
    project.write(".py-harness/ignore", "libs/old\n")
    result = project.make("doctor")
    assert result.stdout.splitlines().count(".") == 2
    assert result.stderr.count("'libs/old' excluded") == 1


def test_a_unit_without_a_package_is_announced(project: Project) -> None:
    (project.root / "src" / "demo" / "__init__.py").unlink()
    result = project.make("doctor")
    assert "doctor: '.' has no package under src; source rules skip it" in result.stderr


def test_doctor_fails_when_units_cannot_resolve(tmp_path: Path) -> None:
    created = Project(tmp_path)
    created.write("Makefile", f"include {SHARE / 'harness.mk'}\n")
    created.write("pyproject.toml", '[tool.uv.workspace]\nmembers = ["libs/*"]\n')
    result = created.make("doctor")
    assert result.returncode != 0
    assert "units: pyproject.toml declares no [project]" in result.stderr


# #region Verdict


def test_a_crashed_check_breaks_the_doctor_and_names_its_error() -> None:
    finished = [
        Checked("rule-a", 0, [], ""),
        Checked("rule-b", CRASHED, [], "Traceback (most recent call last):\nValueError: x\n"),
        Checked("rule-c", 1, ["Header (forbidden):", "  a.py:1  why"], ""),
    ]
    verdict = judged(finished)
    assert verdict.outcome == BROKEN
    assert verdict.headline == "1 passed, 1 failed, 1 crashed"
    assert verdict.first == ["rule-b: ValueError: x", "rule-c: Header (forbidden): a.py:1  why"]
    assert verdict.parts == [
        Part("rule-a", PASSED),
        Part("rule-b", BROKEN, "crashed, exit 70"),
        Part("rule-c", FAILED, "1 finding"),
    ]


def test_a_finding_is_a_line_under_the_heading_at_the_least_indent() -> None:
    cycles = [
        "Import cycles (forbidden):",
        "  cycle in .:",
        "    a -> b (line 1)",
        "    b -> a (line 1)",
        "  cycle in libs:",
        "    c -> d (line 2)",
        "",
        "Rule: the import graph is acyclic.",
    ]
    assert judged([Checked("rule-no-cycles", 1, cycles, "")]).parts[0].headline == "2 findings"


def test_findings_without_a_heading_are_not_counted() -> None:
    assert judged([Checked("rule-a", 1, ["one line"], "")]).parts == [Part("rule-a", FAILED)]


def test_a_passing_check_line_says_what_the_check_said_beyond_ok() -> None:
    skipped = "doctor: boundaries skipped; pyproject.toml declares no contracts"
    finished = [
        Checked("rule-boundaries", 0, [skipped], ""),
        Checked("rule-no-cycles", 0, ["doctor: no-cycles OK"], ""),
        Checked("report-shared-names", 0, ["Names (2):", "  a  x.py, y.py", "  b  x.py, z.py"], ""),
    ]
    assert judged(finished).parts == [
        Part("rule-boundaries", PASSED, "skipped; pyproject.toml declares no contracts"),
        Part("rule-no-cycles", PASSED),
        Part("report-shared-names", PASSED),
    ]


def test_a_finding_without_a_heading_is_given_whole() -> None:
    assert judged([Checked("rule-a", 1, ["one line"], "")]).first == ["rule-a: one line"]


def test_a_check_that_fails_silently_says_so() -> None:
    assert judged([Checked("rule-a", 1, [], "")]).first == ["rule-a: (no output)"]


def test_a_crash_that_printed_no_error_shows_what_it_printed() -> None:
    assert judged([Checked("rule-a", CRASHED, ["partial"], "")]).first == ["rule-a: partial"]


# #region Guard


@pytest.fixture
def isolated(monkeypatch: pytest.MonkeyPatch) -> None:
    """The guard sets the argument list and import path as a script run would; this keeps them."""
    monkeypatch.setattr(sys, "argv", list(sys.argv))
    monkeypatch.setattr(sys, "path", list(sys.path))


@pytest.mark.usefixtures("isolated")
def test_the_guard_tells_a_crash_from_a_finding(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    check = tmp_path / "rule-boom.py"
    check.write_text('raise RuntimeError("boom")\n')
    assert guard.main(["guard", str(check)]) == CRASHED
    assert "RuntimeError: boom" in capsys.readouterr().err


@pytest.mark.usefixtures("isolated")
def test_the_guard_passes_a_checks_own_exit_through(tmp_path: Path) -> None:
    check = tmp_path / "rule-found.py"
    check.write_text("import sys\n\nsys.exit(1)\n")
    with pytest.raises(SystemExit) as exited:
        guard.main(["guard", str(check)])
    assert exited.value.code == 1


@pytest.mark.usefixtures("isolated")
def test_a_guarded_check_imports_its_neighbours(tmp_path: Path) -> None:
    (tmp_path / "helper_for_guard.py").write_text("VALUE = 1\n")
    check = tmp_path / "rule-neighbour.py"
    check.write_text("import helper_for_guard\n\nassert helper_for_guard.VALUE == 1\n")
    assert guard.main(["guard", str(check)]) == 0
