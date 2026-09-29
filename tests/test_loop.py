import shutil
from pathlib import Path

import pytest

from py_harness.loop import main
from py_harness.summary import Snapshot
from py_harness.summary import summarize
from py_harness.suppressions import Suppression
from py_harness.suppressions import Tally
from tests.support import PASSING
from tests.support import Project

LOG_PATH = Path("demo-ci.log")
NOTHING_SUPPRESSED = Tally([], 0, [])
FAILED_LINT = "make: *** [share/harness.mk:84: lint] Error 1\n"


def passing_project(project: Project) -> Project:
    project.write("tests/test_unit.py", PASSING)
    return project


def summary(log: str, status: int = 1, before: Snapshot = None, after: Snapshot = None) -> str:
    lines = summarize(log, status, before or {}, after or {}, NOTHING_SUPPRESSED, LOG_PATH)
    return "\n".join(lines)


# #region Composed runs


def test_check_passes_a_clean_project(project: Project) -> None:
    result = passing_project(project).make("check")
    assert result.returncode == 0
    assert "STATUS: PASSED" in result.stdout


def test_check_fixes_before_it_checks(project: Project) -> None:
    passing_project(project).write("src/demo/shape.py", "x=1\n")
    assert project.make("check").returncode == 0


def test_check_formats_what_a_lint_fix_rewrote(project: Project) -> None:
    passing_project(project).write("src/demo/user.py", "import os\n\n\nVALUE = 1\n")
    assert project.make("check").returncode == 0
    assert project.read("src/demo/user.py") == "VALUE = 1\n"


def test_check_names_the_failing_stage_and_unit(project: Project) -> None:
    project.write("tests/test_unit.py", "def test_fails() -> None:\n    assert 1 + 1 == 3\n")
    result = project.make("check")
    assert result.returncode != 0
    assert "STATUS: FAILED at stage `test` in `.`" in result.stdout
    assert "Test failures (1):\n  FAILED tests/test_unit.py::test_fails" in result.stdout


def test_check_blames_lint_for_what_no_fix_can_repair(project: Project) -> None:
    passing_project(project).write("src/demo/loud.py", 'print("hello")\n')
    assert "STATUS: FAILED at stage `lint`\n" in project.make("check").stdout


def test_check_runs_the_doctor(project: Project) -> None:
    passing_project(project).write("src/demo/a.py", "import demo.b\n\nNEIGHBOUR = demo.b\n")
    project.write("src/demo/b.py", "import demo.a\n\nNEIGHBOUR = demo.a\n")
    assert "STATUS: FAILED at stage `doctor`" in project.make("check").stdout


def test_the_summary_lists_a_rewritten_file_that_was_already_dirty(project: Project) -> None:
    passing_project(project).write("src/demo/shape.py", "x = 1\n")
    project.commit()
    project.write("src/demo/shape.py", "x=2\n")
    listed = "Files modified during run (auto-fix surface, 1):\n  src/demo/shape.py"
    assert listed in project.make("check").stdout


def test_check_outside_git_says_it_cannot_list_modifications(project: Project) -> None:
    shutil.rmtree(passing_project(project).root / ".git")
    unknown = "Files modified during run: unknown outside a git repository"
    assert unknown in project.make("check").stdout


def test_a_tracked_file_deleted_before_the_run_does_not_break_it(project: Project) -> None:
    passing_project(project).write("src/demo/gone.py", "x = 1\n")
    project.commit()
    (project.root / "src" / "demo" / "gone.py").unlink()
    assert project.make("check").returncode == 0


def test_an_unknown_mode_is_a_usage_error(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["loop", "nightly"]) == 2
    assert "usage: python -m py_harness.loop check|ready|ci" in capsys.readouterr().err


# #region Summary


@pytest.mark.parametrize(
    ("line", "label"),
    [
        pytest.param("wiring: no ruff config", "Wiring problems (1):", id="wiring"),
        pytest.param("a.py:1:1: unformatted: File would", "Unformatted files (1):", id="format"),
        pytest.param("a.py:1:1: T201 `print` found", "Ruff diagnostics (1):", id="ruff"),
        pytest.param("  a.py:1:5 - error: Type unknown", "Type errors (1):", id="types"),
        pytest.param("FAILED tests/test_a.py::test_x", "Test failures (1):", id="tests"),
        pytest.param("ERROR: an xfail must prove it", "Test failures (1):", id="refused-run"),
        pytest.param("FAIL Required test coverage of 100.0%", "Coverage (1):", id="coverage"),
    ],
)
def test_the_summary_groups_a_diagnostic_under_its_category(line: str, label: str) -> None:
    assert f"{label}\n  {line}" in summary(f"{line}\n{FAILED_LINT}")


def test_the_summary_truncates_a_long_category() -> None:
    log = "".join(f"src/a.py:{n}:1: T201 `print` found\n" for n in range(1, 26))
    assert "  ... (25 total)" in summary(log)


def test_the_summary_names_a_stage_without_a_unit() -> None:
    assert "STATUS: FAILED at stage `lint`\n" in summary("→ test .\n" + FAILED_LINT)


def test_the_summary_admits_an_indeterminate_stage() -> None:
    assert "STATUS: FAILED (stage indeterminate; see the log tail below)" in summary("boom\n")


def test_the_summary_shows_the_log_tail_when_nothing_is_categorized() -> None:
    assert "No categorized diagnostics; last 25 non-empty lines:\n  boom" in summary("boom\n")


def test_the_summary_names_the_log() -> None:
    assert f"=== summary (full log: {LOG_PATH}) ===" in summary("", status=0)


def test_the_summary_admits_it_cannot_see_modifications_outside_git() -> None:
    lines = summarize("", 0, None, None, NOTHING_SUPPRESSED, LOG_PATH)
    assert "Files modified during run: unknown outside a git repository" in lines


def test_the_summary_lists_a_file_a_fix_reverted() -> None:
    assert "  src/a.py" in summary("", status=0, before={"src/a.py": "one"}, after={})


def test_the_summary_truncates_a_long_modified_list() -> None:
    after = {f"src/{n}.py": "new" for n in range(12)}
    assert "  ... (12 total)" in summary("", status=0, after=after)


def test_check_names_every_failing_stage(project: Project) -> None:
    project.write("src/demo/loud.py", 'print("hello")\n')
    project.write("tests/test_unit.py", "def test_fails() -> None:\n    assert 1 + 1 == 3\n")
    assert "STATUS: FAILED at stages `lint`, `test` in `.`" in project.make("check").stdout


def test_the_summary_groups_doctor_findings_with_their_header() -> None:
    log = "Re-exports (forbidden):\n  src/a.py:1  `os as os`\n\nRule: ...\n"
    assert "Doctor findings (2):\n  Re-exports (forbidden):\n    src/a.py:1  `os as os`" in summary(
        log
    )


def test_the_summary_lists_the_lines_no_test_runs() -> None:
    row = "src/demo/calc.py       4      1      0      0    75%   6"
    assert f"Uncovered lines (file, then the lines no test runs) (1):\n  {row}" in summary(
        row + "\n"
    )


def suppression(line: int, label: str) -> Suppression:
    return Suppression("src/a.py", line, label, "# ...")


def test_the_summary_counts_suppressions_with_their_change_and_most_common_rules() -> None:
    present = [
        suppression(1, "noqa E501"),
        suppression(2, "noqa E501"),
        suppression(3, "pyright reportAny"),
    ]
    lines = summarize("", 0, {}, {}, Tally(present, 1, present[2:]), LOG_PATH)
    common = "(most: noqa E501 x2, pyright reportAny x1)"
    headline = f"Suppressions: 3 in the repository, +1 since the last commit {common}"
    assert headline in lines
    assert "Added since the last commit (1):" in lines
    assert "  src/a.py:3  pyright reportAny" in lines


def test_the_summary_says_when_nothing_is_suppressed() -> None:
    lines = summarize("", 0, {}, {}, NOTHING_SUPPRESSED, LOG_PATH)
    assert "Suppressions: none in the repository, unchanged since the last commit" in lines


def test_the_summary_admits_the_change_is_unknown_without_history() -> None:
    lines = summarize("", 0, {}, {}, Tally([suppression(1, "noqa E501")], None, None), LOG_PATH)
    unknown = "Suppressions: 1 in the repository; the change is unknown without a commit to compare"
    assert f"{unknown} (most: noqa E501 x1)" in lines


def test_the_summary_truncates_a_long_list_of_added_suppressions() -> None:
    added = [suppression(number, "noqa E501") for number in range(25)]
    assert "  ... (25 total)" in summarize("", 0, {}, {}, Tally(added, 25, added), LOG_PATH)
