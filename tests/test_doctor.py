from pathlib import Path

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
        "agent OK",
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
    stdout = project.make("doctor").stdout
    expected = (
        "doctor: no-secrets OK",
        "local rule",
        "doctor: agent OK",
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
    assert (
        "doctor: no-reexport OK\ndoctor: no-secrets OK\n\na1\na2\n\nb1\nb2\n\ndoctor: agent OK\n"
        in (stdout)
    )
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
