import json
import subprocess
from pathlib import Path

import pytest

from py_harness import stage
from py_harness.pytest_plugin import REPORT_VARIABLE
from py_harness.pytest_plugin import cause
from py_harness.verdict import BROKEN
from py_harness.verdict import PASSED
from py_harness.verdict import VERDICT_VARIABLE
from py_harness.verdict import Verdict
from py_harness.verdict import read_verdict
from tests.support import PASSING
from tests.support import SHARE
from tests.support import VENV
from tests.support import Project
from tests.support import declare_layout
from tests.support import harness_environment
from tests.support import manifest

FAILING = "def test_fails() -> None:\n    assert False\n"
NOISY_FAILING = """import logging


def test_charge() -> None:
    print("balance before: 10")
    logging.getLogger("bank").warning("card declined: limit 5")
    assert False
"""
MARKED = "import pytest\n\npytestmark = pytest.mark.integration\n\n\n"
IMPORTABLE = '\n[tool.pytest.ini_options]\npythonpath = ["src"]\n'


def wired_workspace(root: Path, *members: str) -> Project:
    created = Project(root)
    created.write("Makefile", f"include {SHARE / 'harness.mk'}\n")
    root_table = (
        f"[tool.uv.workspace]\nmembers = {list(members)!r}\n\n"
        f'[tool.ruff]\nextend = "{SHARE / "ruff.toml"}"\n\n'
        f'[tool.basedpyright]\nextends = "{SHARE / "pyrightconfig.json"}"\n'
    )
    created.write("pyproject.toml", root_table)
    for path in members:
        name = Path(path).name
        created.write(f"{path}/pyproject.toml", f'[project]\nname = "{name}"\nversion = "0.0.0"\n')
        created.write(f"{path}/src/{name.replace('-', '_')}/__init__.py")
    return created


def importable(project: Project) -> None:
    project.write("pyproject.toml", manifest() + IMPORTABLE)


# #region Test


def test_test_runs_the_tests_outside_integration(project: Project) -> None:
    project.write("tests/test_unit.py", PASSING)
    project.write("tests/test_slow.py", MARKED + FAILING)
    result = project.make("test")
    assert result.returncode == 0
    assert "→ test ." in result.stdout


def test_test_files_may_share_a_name_in_different_folders(project: Project) -> None:
    project.write("tests/unit/test_same.py", PASSING)
    project.write("tests/integration/test_same.py", PASSING)
    result = project.make("test")
    assert result.returncode == 0, result.stdout
    assert "2 passed" in result.stdout


def test_each_units_line_starts_a_paragraph(project: Project) -> None:
    project.write("tests/test_unit.py", PASSING)
    assert project.make("test").stdout.startswith("\n→ test .\n")


def test_test_fails_on_a_failing_test(project: Project) -> None:
    project.write("tests/test_unit.py", FAILING)
    result = project.make("test")
    assert result.returncode != 0
    assert "FAILED tests/test_unit.py::test_fails" in result.stdout


def test_test_skips_a_unit_without_tests_and_says_so(project: Project) -> None:
    result = project.make("test")
    assert result.returncode == 0
    assert "·  skip test . (no tests at tests)" in result.stdout


def test_test_stops_at_the_first_failing_unit(tmp_path: Path) -> None:
    created = wired_workspace(tmp_path, "libs/a", "libs/b")
    created.write("libs/a/tests/test_a.py", FAILING)
    created.write("libs/b/tests/test_b.py", PASSING)
    result = created.make("test")
    assert result.returncode != 0
    assert "→ test libs/b" not in result.stdout


def test_pytest_rejects_an_unregistered_marker(project: Project) -> None:
    project.write("tests/test_unit.py", "import pytest\n\n\n@pytest.mark.slowish\n" + PASSING)
    assert project.make("test").returncode != 0


def test_an_xfail_that_passes_fails(project: Project) -> None:
    project.write("tests/test_unit.py", "import pytest\n\n\n@pytest.mark.xfail\n" + PASSING)
    assert project.make("test").returncode != 0


@pytest.mark.parametrize("setting", ["strict", "run"])
def test_an_xfail_that_cannot_prove_its_test_fails_is_refused(
    project: Project, setting: str
) -> None:
    marker = f"import pytest\n\n\n@pytest.mark.xfail({setting}=False)\n"
    project.write("tests/test_unit.py", marker + PASSING)
    result = project.make("test")
    assert result.returncode != 0
    assert f"tests/test_unit.py::test_passes ({setting}=False)" in result.stderr + result.stdout


def test_the_callers_pytest_arguments_are_dropped(project: Project) -> None:
    project.write("tests/test_unit.py", FAILING)
    deselecting = {"PYTEST_ADDOPTS": "--deselect tests/test_unit.py"}
    assert project.make("test", environment=deselecting).returncode != 0


def test_a_stage_reports_units_it_cannot_resolve(tmp_path: Path) -> None:
    created = wired_workspace(tmp_path)
    result = created.make("test")
    assert result.returncode != 0
    assert "units: pyproject.toml declares no [project]" in result.stderr


def test_an_unknown_stage_is_refused(capsys: pytest.CaptureFixture[str]) -> None:
    assert stage.main(["stage", str(SHARE), "nightly"]) == 2
    expected = "unknown stage: nightly (expected format-fix, lint-fix, lint-fix-unsafe, format,"
    assert expected in capsys.readouterr().err


# #region Integration


def test_test_integration_runs_only_integration_tests(project: Project) -> None:
    project.write("tests/test_unit.py", FAILING)
    project.write("tests/test_slow.py", MARKED + PASSING)
    result = project.make("test-integration")
    assert result.returncode == 0
    assert "1 passed, 1 deselected" in result.stdout


def test_test_integration_passes_when_no_test_is_marked(project: Project) -> None:
    project.write("tests/test_unit.py", PASSING)
    result = project.make("test-integration")
    assert result.returncode == 0
    assert "·  skip test-integration . (no test is marked integration)" in result.stdout


def test_test_passes_when_every_test_is_marked_integration(project: Project) -> None:
    project.write("tests/test_slow.py", MARKED + PASSING)
    result = project.make("test")
    assert result.returncode == 0
    assert "·  skip test . (every test is marked integration)" in result.stdout


# #region One unit


def test_test_pkg_runs_only_the_named_unit(tmp_path: Path) -> None:
    created = wired_workspace(tmp_path, "libs/a", "libs/b")
    created.write("libs/a/tests/test_a.py", FAILING)
    created.write("libs/b/tests/test_b.py", PASSING)
    result = created.make("test-pkg", "PKG=b")
    assert result.returncode == 0
    assert "→ test libs/a" not in result.stdout


def test_a_unit_is_found_by_its_normalized_name(tmp_path: Path) -> None:
    created = wired_workspace(tmp_path, "libs/lib-b")
    created.write("libs/lib-b/tests/test_b.py", PASSING)
    assert "→ test libs/lib-b" in created.make("test-pkg", "PKG=Lib_B").stdout


def test_a_pkg_target_requires_pkg(project: Project) -> None:
    result = project.make("test-pkg")
    assert result.returncode != 0
    assert "PKG=<project name> is required" in result.stderr


def test_an_unknown_package_lists_the_available_ones(project: Project) -> None:
    result = project.make("test-pkg", "PKG=nope")
    assert result.returncode != 0
    assert "unknown package: nope\navailable:\n  demo  (.)" in result.stdout


def test_typecheck_pkg_checks_only_the_named_unit(tmp_path: Path) -> None:
    created = wired_workspace(tmp_path, "libs/a", "libs/b")
    created.write("libs/a/src/a/broken.py", "def f(x):\n    return x\n")
    assert created.make("typecheck-pkg", "PKG=b").returncode == 0
    assert created.make("typecheck-pkg", "PKG=a").returncode != 0


# #region Coverage

HALF_TESTED = "def used() -> int:\n    return 1\n\n\ndef unused() -> int:\n    return 2\n"
TESTS_USED = "from demo.calc import used\n\n\ndef test_used() -> None:\n    assert used() == 1\n"


def test_coverage_fails_below_full_branch_coverage(project: Project) -> None:
    importable(project)
    project.write("src/demo/calc.py", HALF_TESTED)
    project.write("tests/test_calc.py", TESTS_USED)
    result = project.make("coverage")
    assert result.returncode != 0
    assert "FAIL Required test coverage of 100.0% not reached" in result.stdout


def test_coverage_passes_at_full_branch_coverage(project: Project) -> None:
    importable(project)
    project.write("src/demo/calc.py", "def used() -> int:\n    return 1\n")
    project.write("tests/test_calc.py", TESTS_USED)
    assert project.make("coverage").returncode == 0


def test_a_no_cover_pragma_excludes_nothing(project: Project) -> None:
    importable(project)
    project.write(
        "src/demo/calc.py",
        HALF_TESTED.replace("def unused() -> int:", "def unused() -> int:  # pragma: no cover"),
    )
    project.write("tests/test_calc.py", TESTS_USED)
    assert project.make("coverage").returncode != 0


@pytest.mark.parametrize(
    "block",
    [
        pytest.param(
            "from typing import TYPE_CHECKING\n\nif TYPE_CHECKING:\n    import os\n",
            id="type-checking",
        ),
        pytest.param('if __name__ == "__main__":\n    raise SystemExit(used())\n', id="main-guard"),
        pytest.param(
            "from typing import overload\n\n\n@overload\ndef pick(x: int) -> int: ...\n"
            "@overload\ndef pick(x: str) -> str: ...\n"
            "def pick(x: int | str) -> int | str:\n    return x\n\n\n"
            "pick(1)\n",
            id="overload",
        ),
    ],
)
def test_coverage_exempts_code_that_never_runs_under_test(project: Project, block: str) -> None:
    importable(project)
    project.write("src/demo/calc.py", f"def used() -> int:\n    return 1\n\n\n{block}")
    project.write("tests/test_calc.py", TESTS_USED)
    assert project.make("coverage").returncode == 0


def test_coverage_counts_code_run_in_a_subprocess(project: Project) -> None:
    importable(project)
    project.write("src/demo/calc.py", "def used() -> int:\n    return 1\n")
    project.write("src/demo/tool.py", 'print("ran")  # noqa: T201\n')
    project.write(
        "tests/test_calc.py",
        TESTS_USED
        + "\n\ndef test_tool() -> None:\n"
        + "    import os, subprocess, sys\n"
        + '    env = {**os.environ, "PYTHONPATH": "src"}\n'
        + '    subprocess.run([sys.executable, "-m", "demo.tool"], env=env, check=True)\n',
    )
    assert project.make("coverage").returncode == 0


def test_coverage_leaves_no_data_file_in_the_repository(project: Project) -> None:
    importable(project)
    project.write("src/demo/calc.py", "def used() -> int:\n    return 1\n")
    project.write("tests/test_calc.py", TESTS_USED)
    assert project.make("coverage").returncode == 0
    assert sorted(path.name for path in project.root.glob(".coverage*")) == []


def test_coverage_refuses_a_unit_with_source_and_no_tests(project: Project) -> None:
    result = project.make("coverage")
    assert result.returncode != 0
    assert "coverage: . has src and no tests at tests" in result.stderr


def test_coverage_skips_a_unit_without_source(project: Project) -> None:
    (project.root / "src" / "demo" / "__init__.py").unlink()
    (project.root / "src" / "demo").rmdir()
    (project.root / "src").rmdir()
    result = project.make("coverage")
    assert result.returncode == 0
    assert "·  skip coverage . (no src)" in result.stdout


def test_coverage_refuses_to_run_beside_a_config_it_would_ignore(project: Project) -> None:
    project.write("pyproject.toml", manifest() + "\n[tool.coverage.report]\nfail_under = 50\n")
    result = project.make("coverage")
    assert result.returncode != 0
    assert "wiring: pyproject.toml: coverage runs on the harness's configuration" in result.stderr


# #region Declared layout


def test_test_runs_the_declared_suite(project: Project) -> None:
    declare_layout(project)
    project.write("src/test/test_unit.py", FAILING)
    assert "FAILED src/test/test_unit.py::test_fails" in project.make("test").stdout


def test_coverage_measures_the_declared_source(project: Project) -> None:
    declare_layout(project)
    project.write("src/main/demo/calc.py", "def used() -> int:\n    return 1\n")
    project.write("src/test/test_calc.py", TESTS_USED)
    target = project.root.parent / "coverage.verdict"
    result = project.make("coverage", environment={VERDICT_VARIABLE: str(target)})
    assert result.returncode == 0
    assert "Required test coverage of 100.0% reached" in result.stdout
    assert read_verdict(target) == Verdict(PASSED, "1 passed, 0 failed, 100% of src/main")


# #region Verdicts


def ran(reports: Path, code: int, stdout: str = "", measured: Path | None = None) -> stage.Ran:
    return stage.Ran(code, stdout, reports, measured)


def measured(reports: Path, percent: float, gaps: dict[str, dict[str, list[int]]]) -> None:
    content = {"totals": {"percent_covered": percent}, "files": gaps}
    (reports / stage.COVERAGE_REPORT).write_text(json.dumps(content))


def recorded(reports: Path, passed: int, *failures: str) -> None:
    problems = [{"test": test, "message": "boom", "text": "trace"} for test in failures]
    content = {"rootdir": str(reports), "passed": passed, "problems": problems}
    (reports / stage.TEST_REPORT).write_text(json.dumps(content))


def test_every_unit_passing_is_one_pass(tmp_path: Path) -> None:
    created = wired_workspace(tmp_path / "repo", "libs/a", "libs/b")
    created.write("libs/a/tests/test_a.py", PASSING)
    created.write("libs/b/tests/test_b.py", PASSING)
    target = tmp_path / "test.verdict"
    assert created.make("test", environment={VERDICT_VARIABLE: str(target)}).returncode == 0
    assert read_verdict(target) == Verdict(PASSED, "2 passed, 0 failed")


def test_the_unit_a_stage_stopped_at_says_how_many_never_ran(tmp_path: Path) -> None:
    created = wired_workspace(tmp_path / "repo", "libs/a", "libs/b")
    created.write("libs/a/tests/test_a.py", FAILING)
    target = tmp_path / "test.verdict"
    created.make("test", environment={VERDICT_VARIABLE: str(target)})
    verdict = read_verdict(target)
    assert verdict is not None
    assert verdict.headline == "0 passed, 1 failed; 1 unit after it not run"


def absent_checker(_unit: Path | None, _harness: Path, _reports: Path) -> stage.Step:
    return stage.Run(["no-such-checker-anywhere"])


def test_a_checker_that_cannot_start_is_broken(tmp_path: Path) -> None:
    absent = stage.Stage(absent_checker, stage.judge_fix)
    verdict = stage.perform("lint", None, absent, tmp_path, tmp_path, {}).verdict
    assert verdict.outcome == BROKEN
    assert verdict.headline.startswith("could not start no-such-checker-anywhere: ")


def test_a_checker_exit_beyond_found_is_broken_and_its_output_kept(tmp_path: Path) -> None:
    judged = stage.judge_typecheck(ran(tmp_path, 2, "Fatal error"), tmp_path)
    assert judged.verdict == Verdict(BROKEN, "basedpyright exited 2")
    assert judged.shown == ["Fatal error"]


def test_a_found_exit_with_an_empty_report_is_broken(tmp_path: Path) -> None:
    judged = stage.judge_typecheck(ran(tmp_path, 1, '{"generalDiagnostics": []}'), tmp_path)
    assert judged.verdict == Verdict(
        BROKEN, "basedpyright exited 1 and its report could not be read"
    )


def test_a_test_session_that_failed_and_recorded_nothing_is_broken(tmp_path: Path) -> None:
    assert stage.judge_tests(ran(tmp_path, 3), tmp_path).verdict == Verdict(
        BROKEN, "pytest exited 3"
    )


def test_a_clean_test_session_without_its_record_proves_no_pass(tmp_path: Path) -> None:
    assert stage.judge_tests(ran(tmp_path, 0), tmp_path).verdict.outcome == BROKEN


def test_a_passing_session_says_how_many_tests_passed_and_failed(tmp_path: Path) -> None:
    recorded(tmp_path, 3)
    assert stage.judge_tests(ran(tmp_path, 0), tmp_path).verdict.headline == "3 passed, 0 failed"


def test_a_measured_session_says_how_much_of_which_source_its_tests_run(tmp_path: Path) -> None:
    recorded(tmp_path, 3)
    measured(tmp_path, 100.0, {})
    judged = stage.judge_tests(ran(tmp_path, 0, measured=Path("libs/a/src")), tmp_path)
    assert judged.verdict == Verdict(PASSED, "3 passed, 0 failed, 100% of libs/a/src")


def test_a_recorded_failure_fails_whatever_pytest_exits_with(tmp_path: Path) -> None:
    recorded(tmp_path, 2, "tests/test_a.py::test_x")
    assert stage.judge_tests(ran(tmp_path, 0), tmp_path).verdict.headline == "2 passed, 1 failed"


def test_a_session_cut_short_without_a_failure_proves_no_pass(tmp_path: Path) -> None:
    recorded(tmp_path, 2)
    assert stage.judge_tests(ran(tmp_path, 2), tmp_path).verdict.outcome == BROKEN


def test_a_measured_session_without_its_measure_proves_no_pass(tmp_path: Path) -> None:
    recorded(tmp_path, 2)
    judged = stage.judge_tests(ran(tmp_path, 0, measured=Path("src")), tmp_path)
    assert judged.verdict.outcome == BROKEN


def test_coverage_names_a_failing_test_before_any_gap(tmp_path: Path) -> None:
    recorded(tmp_path, 2, "tests/test_a.py::test_x")
    measured(tmp_path, 50.0, {"src/a.py": {"missing_lines": [7], "missing_branches": []}})
    verdict = stage.judge_tests(ran(tmp_path, 1, measured=Path("src")), tmp_path).verdict
    assert verdict.headline == "2 passed, 1 failed, 50% of src"
    assert verdict.first == ["tests/test_a.py::test_x: boom"]


def test_a_session_stopped_before_measuring_names_no_share(tmp_path: Path) -> None:
    recorded(tmp_path, 0, "tests/test_a.py")
    verdict = stage.judge_tests(ran(tmp_path, 2, measured=Path("src")), tmp_path).verdict
    assert verdict.headline == "0 passed, 1 failed"


def test_coverage_without_its_report_is_broken(tmp_path: Path) -> None:
    recorded(tmp_path, 2)
    judged = stage.judge_tests(ran(tmp_path, 1, measured=Path("src")), tmp_path)
    assert judged.verdict.outcome == BROKEN


def test_coverage_short_of_full_never_reads_as_full(tmp_path: Path) -> None:
    recorded(tmp_path, 2)
    measured(tmp_path, 99.96, {"src/a.py": {"missing_lines": [7], "missing_branches": []}})
    verdict = stage.judge_tests(ran(tmp_path, 1, measured=Path("src")), tmp_path).verdict
    assert verdict.headline == "2 passed, 0 failed, 99.9% of src"
    assert verdict.first == ["src/a.py: lines 7"]


def test_several_units_add_up_to_one_line(tmp_path: Path) -> None:
    def unit(passed: int, source: str) -> stage.Judged:
        tested = stage.Tested(passed, 0, stage.Share(100.0, source))
        return stage.Judged(Verdict(PASSED, tested.headline()), tested=tested)

    skipped = stage.Judged(Verdict(PASSED, "skipped coverage libs/c: no libs/c/src"))
    verdict = stage.together([unit(3, "libs/a/src"), skipped, unit(2, "libs/b/src")])
    assert verdict == Verdict(PASSED, "5 passed, 0 failed, 100% of 2 units")


def test_a_failing_test_keeps_what_it_printed_and_logged(project: Project) -> None:
    project.write("tests/test_unit.py", NOISY_FAILING)
    target = project.root.parent / "test.verdict"
    project.make("test", environment={VERDICT_VARIABLE: str(target)})
    verdict = read_verdict(target)
    assert verdict is not None
    [section] = verdict.sections
    assert "balance before: 10" in section.lines
    assert "WARNING  bank:test_unit.py:6 card declined: limit 5" in section.lines


def test_a_failure_without_a_marked_line_gives_its_last_line() -> None:
    assert cause("first\nlast\n", "call") == "last"
    assert cause("", "setup") == "in setup: "


def test_an_unproven_xfail_is_refused_outside_a_stage_too(project: Project) -> None:
    marker = "import pytest\n\n\n@pytest.mark.xfail(strict=False)\n"
    project.write("tests/test_unit.py", marker + PASSING)
    environment = {**harness_environment(), "PYTHONPATH": "src"}
    environment.pop(REPORT_VARIABLE, None)
    result = subprocess.run(
        [str(VENV / "bin" / "pytest"), "-p", "no:cacheprovider", "tests"],
        cwd=project.root,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == pytest.ExitCode.USAGE_ERROR
    assert "an xfail must prove its test still fails" in result.stderr
