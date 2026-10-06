import os
import shutil
import signal
import subprocess
import time
from pathlib import Path

import pytest

from py_harness import loop
from py_harness.logs import RUNNING
from py_harness.logs import RunRecord
from py_harness.logs import StageRecord
from py_harness.logs import log_folder
from py_harness.logs import read_record
from py_harness.loop import LIMIT_VARIABLE
from py_harness.loop import decided
from py_harness.loop import main
from py_harness.loop import run_stage
from py_harness.loop import summary
from py_harness.summary import bare
from py_harness.summary import changed_files
from py_harness.summary import render
from py_harness.summary import suppression_lines
from py_harness.suppressions import Suppression
from py_harness.suppressions import Tally
from py_harness.verdict import BROKEN
from py_harness.verdict import FAILED
from py_harness.verdict import PASSED
from py_harness.verdict import SKIPPED
from py_harness.verdict import Verdict
from tests.support import PASSING
from tests.support import VENV
from tests.support import Project
from tests.support import harness_environment
from tests.support import manifest

LOGS = ".git/py-harness/logs/check"
NOTHING_SUPPRESSED = Tally([], 0, [])
FAILING = "def test_fails() -> None:\n    assert 1 + 1 == 3\n"
LOUD = 'print("hello")\n'
# A test that leaves its process id where the test can find it, then never ends.
HANGING = """
import os
import time
from pathlib import Path


def test_hangs() -> None:
    Path(os.environ["HANGING_MARKER"]).write_text(str(os.getpid()))
    time.sleep(120)
"""
MARKER = "HANGING_MARKER"
UNREADABLE_RUFF_SETTING = "[tool.ruff]\nsrc = 5\n"
LIMIT = "8"


def passing_project(project: Project) -> Project:
    project.write("tests/test_unit.py", PASSING)
    return project


def hanging(project: Project) -> Path:
    project.write("tests/test_slow.py", HANGING)
    return project.root.parent / "hanging.pid"


def alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    return True


def last_line(text: str) -> str:
    return text.splitlines()[-1]


# #region Composed runs


def test_check_passes_a_clean_project(project: Project) -> None:
    result = passing_project(project).make("check")
    assert result.returncode == 0
    assert last_line(result.stdout).startswith("check passed in ")
    assert last_line(result.stdout).endswith(f"s. Logs: {LOGS}")


def test_check_prints_one_line_per_stage(project: Project) -> None:
    stdout = passing_project(project).make("check").stdout
    named = [line.split()[0] for line in stdout.splitlines() if line.startswith("  ")]
    assert named == list(loop.LOOPS["check"])


def test_check_keeps_each_stages_output_in_its_log(project: Project) -> None:
    result = passing_project(project).make("check")
    assert "test session starts" not in result.stdout
    assert "test session starts" in project.read(f"{LOGS}/test.log")


def test_check_fixes_before_it_checks(project: Project) -> None:
    passing_project(project).write("src/demo/shape.py", "x=1\n")
    assert project.make("check").returncode == 0


def test_check_formats_what_a_lint_fix_rewrote(project: Project) -> None:
    passing_project(project).write("src/demo/user.py", "import os\n\n\nVALUE = 1\n")
    assert project.make("check").returncode == 0
    assert project.read("src/demo/user.py") == "VALUE = 1\n"


def test_a_fix_counts_the_files_it_rewrote(project: Project) -> None:
    passing_project(project).write("src/demo/shape.py", "x=1\n")
    assert "  format-fix  ok       rewrote 1 file (" in project.make("check").stdout


def test_check_shows_a_failing_stages_findings_counted_per_rule(project: Project) -> None:
    passing_project(project).write("src/demo/loud.py", LOUD)
    stdout = project.make("check").stdout
    shown = (
        "lint: 1 finding in 1 file\n  by rule: T201 1\n  src/demo/loud.py:1:1: T201 `print` found\n"
    )
    assert shown in stdout
    assert f"  all of them: {LOGS}/lint.txt" in stdout
    assert last_line(stdout) == f"check did not pass: failed lint. Logs: {LOGS}"


def test_a_findings_file_holds_a_section_per_file(project: Project) -> None:
    passing_project(project).write("src/demo/loud.py", LOUD)
    project.make("check")
    heading = "# lint: 1 finding in 1 file\n# by rule: T201 1\n"
    section = "\n## src/demo/loud.py (1)\n1:1: T201 `print` found\n"
    assert project.read(f"{LOGS}/lint.txt") == heading + section


def test_check_names_a_failing_test_and_its_error(project: Project) -> None:
    project.write("tests/test_unit.py", FAILING)
    stdout = project.make("check").stdout
    named = "test: 0 passed, 1 failed\n  tests/test_unit.py::test_fails: assert (1 + 1) == 3\n"
    assert named in stdout


def test_check_runs_the_doctor(project: Project) -> None:
    passing_project(project).write("src/demo/a.py", "import demo.b\n\nNEIGHBOUR = demo.b\n")
    project.write("src/demo/b.py", "import demo.a\n\nNEIGHBOUR = demo.a\n")
    stdout = project.make("check").stdout
    assert "  rule-no-cycles: Import cycles (forbidden): cycle in .:" in stdout


def test_check_names_every_failing_stage(project: Project) -> None:
    project.write("src/demo/loud.py", LOUD)
    project.write("tests/test_unit.py", FAILING)
    expected = f"check did not pass: failed lint, test. Logs: {LOGS}"
    assert last_line(project.make("check").stdout) == expected


def test_the_summary_lists_a_rewritten_file_that_was_already_dirty(project: Project) -> None:
    passing_project(project).write("src/demo/shape.py", "x = 1\n")
    project.commit()
    project.write("src/demo/shape.py", "x=2\n")
    listed = "Files changed during the run (1): src/demo/shape.py"
    assert listed in project.make("check").stdout


def test_check_outside_git_says_it_cannot_list_modifications(project: Project) -> None:
    shutil.rmtree(passing_project(project).root / ".git")
    unknown = "Files changed during the run: unknown outside a git repository"
    assert unknown in project.make("check").stdout


def test_check_outside_git_keeps_its_logs_in_a_temporary_folder(project: Project) -> None:
    shutil.rmtree(passing_project(project).root / ".git")
    folder = log_folder(project.root, "check")
    assert last_line(project.make("check").stdout).endswith(f"Logs: {folder.as_posix()}")
    assert (folder / "test.log").is_file()


def test_a_tracked_file_deleted_before_the_run_does_not_break_it(project: Project) -> None:
    passing_project(project).write("src/demo/gone.py", "x = 1\n")
    project.commit()
    (project.root / "src" / "demo" / "gone.py").unlink()
    assert project.make("check").returncode == 0


def test_a_run_clears_what_an_earlier_run_left(project: Project) -> None:
    stale = passing_project(project).write(f"{LOGS}/stale.txt", "old")
    project.make("check")
    assert not stale.exists()


def test_an_unknown_mode_is_a_usage_error(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["loop", "nightly"]) == 2
    assert "usage: python -m py_harness.loop check|ready|ci|last" in capsys.readouterr().err


def test_wiring_problems_skip_the_stages_that_need_the_wiring(project: Project) -> None:
    unwired = '[project]\nname = "demo"\nversion = "0.0.0"\n'
    passing_project(project).write("pyproject.toml", unwired)
    stdout = project.make("check").stdout
    assert "  wiring      failed   (" in stdout
    skipped = (
        "  lint        skipped  not run: a tool config does not reach the harness's (see wiring)"
    )
    assert skipped in stdout
    assert "    wiring: no ruff configuration at the project root; ruff would run" in stdout


def test_ci_shows_each_stages_output(
    project: Project, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    for name in ("VIRTUAL_ENV", "MAKEFLAGS", "MFLAGS", "MAKELEVEL", "PY_HARNESS_UNITS"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("UV_PROJECT_ENVIRONMENT", str(VENV))
    monkeypatch.chdir(passing_project(project).root)
    folder = project.root / LOGS
    folder.mkdir(parents=True)
    assert run_stage("lint", folder, project.root, 60, stream=True).outcome == PASSED
    assert "→ lint" in capsys.readouterr().out


# #region What a run says when something breaks


def test_a_tool_that_cannot_read_its_config_is_broken_not_failed(project: Project) -> None:
    unreadable = manifest().replace("[tool.ruff]\n", UNREADABLE_RUFF_SETTING)
    passing_project(project).write("pyproject.toml", unreadable)
    result = project.make("check")
    assert result.returncode != 0
    assert "  lint        BROKEN   ruff exited 2 (" in result.stdout
    told = "lint-fix broke: ruff exited 2.\n  Its result is unknown; this is not a finding in the"
    assert told in result.stdout
    assert "invalid type: integer `5`, expected a sequence" in result.stdout
    assert "passed" not in last_line(result.stdout)


def test_stages_broken_by_one_cause_tell_it_once(project: Project) -> None:
    unreadable = manifest().replace("[tool.ruff]\n", UNREADABLE_RUFF_SETTING)
    passing_project(project).write("pyproject.toml", unreadable)
    stdout = project.make("check").stdout
    assert "format-fix broke the same way as lint-fix; all of its output: " in stdout
    assert stdout.count("expected a sequence") == 1


def test_a_crashing_doctor_check_is_broken_with_its_error(project: Project) -> None:
    passing_project(project).write(
        ".py-harness/doctor/rule-boom.py", 'raise RuntimeError("boom")\n'
    )
    stdout = project.make("check").stdout
    assert "doctor broke: rule-boom crashed.\n" in stdout
    assert "  rule-boom: RuntimeError: boom\n" in stdout


def test_a_stage_past_its_limit_is_stopped_with_all_it_started(project: Project) -> None:
    marker = hanging(passing_project(project))
    limited = {LIMIT_VARIABLE: LIMIT, MARKER: str(marker)}
    result = project.make("check", environment=limited)
    assert f"test broke: stopped after {LIMIT}s, still running." in result.stdout
    assert not alive(int(marker.read_text()))


def test_a_run_told_to_stop_ends_its_stage_and_says_where_it_stopped(project: Project) -> None:
    marker = hanging(passing_project(project))
    started = subprocess.Popen(
        ["make", "-s", "check"],  # noqa: S607
        cwd=project.root,
        env={**harness_environment(), MARKER: str(marker)},
        stdout=subprocess.PIPE,
        text=True,
        process_group=0,
    )
    deadline = time.monotonic() + 120
    while not marker.exists() and time.monotonic() < deadline:
        time.sleep(0.2)
    os.killpg(started.pid, signal.SIGTERM)
    stdout, _ = started.communicate(timeout=60)
    assert "test: the run stopped during this stage; its output so far: " in stdout
    assert last_line(stdout) == f"check did not pass: interrupted test. Logs: {LOGS}"
    assert not alive(int(marker.read_text()))
    recorded = read_record(project.root / LOGS)
    assert recorded is not None
    assert not recorded.finished


def test_make_last_draws_the_newest_run_again(project: Project) -> None:
    passing_project(project).write("src/demo/loud.py", LOUD)
    first = project.make("check").stdout
    again = project.make("last")
    assert again.returncode == 0
    assert "  lint        failed   1 finding in 1 file (" in again.stdout
    assert last_line(again.stdout) == last_line(first)


def test_make_last_says_when_nothing_ran(project: Project) -> None:
    expected = "No check, ready or ci run is recorded in this clone yet.\n"
    assert project.make("last").stdout == expected


# #region A stage's verdict, held to its exit code


def test_a_stage_stopped_at_its_limit_is_broken() -> None:
    assert decided("test", None, None, 3) == Verdict(BROKEN, "stopped after 3s, still running")


def test_a_reporting_stage_that_left_no_verdict_is_broken() -> None:
    assert decided("lint", 2, None, 900) == Verdict(BROKEN, "stopped before it reported, exit 2")


def test_a_stage_that_never_reports_is_judged_by_its_exit_code() -> None:
    assert decided("agent", 0, None, 900) == Verdict(PASSED)
    assert decided("agent", 2, None, 900) == Verdict(FAILED)


def test_a_reported_pass_that_exited_badly_is_broken() -> None:
    expected = Verdict(BROKEN, "reported a pass but exited 2")
    assert decided("lint", 2, Verdict(PASSED), 900) == expected


def test_a_reported_finding_stands() -> None:
    assert decided("lint", 2, Verdict(FAILED, "1 finding"), 900) == Verdict(FAILED, "1 finding")


# #region Summary


def stage(
    name: str = "lint",
    outcome: str = FAILED,
    counts: dict[str, int] | None = None,
    first: list[str] | None = None,
) -> StageRecord:
    return StageRecord(name, outcome, 0.5, "", counts or {}, first or [], f"{name}.log", None)


def drawn(*stages: StageRecord, root: Path = Path(), finished: bool = True) -> str:
    record = RunRecord("check", "2026-10-05T00:00:00+00:00", finished, list(stages), [])
    return "\n".join(render(record, root, LOGS, table=False))


def test_the_summary_names_at_most_six_rules() -> None:
    counts = {f"R{number}": 1 for number in range(8)}
    shown = drawn(stage(counts=counts, first=["a.py:1:1: R0 x"]))
    assert "  by rule: R0 1, R1 1, R2 1, R3 1, R4 1, R5 1, ..." in shown


def test_a_failed_stage_without_findings_shows_its_last_lines(tmp_path: Path) -> None:
    log = "→ agent\nagent: .claude/rules/x.md is not a link\nmake[1]: *** Error 1\n"
    (tmp_path / "agent.log").write_text(log)
    shown = drawn(stage("agent"), root=tmp_path)
    assert "  last lines of its output:\n    agent: .claude/rules/x.md is not a link\n" in shown
    assert "make[1]" not in shown


def test_the_summary_admits_a_log_it_cannot_read(tmp_path: Path) -> None:
    assert "  (its output could not be read)" in drawn(stage("agent"), root=tmp_path)


def test_the_summary_admits_a_stage_that_printed_nothing(tmp_path: Path) -> None:
    (tmp_path / "agent.log").write_text("→ agent\n")
    assert "  (it printed nothing)" in drawn(stage("agent"), root=tmp_path)


def test_a_stage_the_record_still_holds_as_running_was_interrupted() -> None:
    shown = drawn(stage("test", RUNNING), finished=False)
    assert "test: the run stopped during this stage; its output so far: test.log" in shown
    assert shown.endswith(f"check did not pass: interrupted test. Logs: {LOGS}")


def test_the_verdict_names_every_outcome_in_order(tmp_path: Path) -> None:
    stages = [
        stage("lint"),
        stage("test", RUNNING),
        stage("format", SKIPPED),
        stage("doctor", BROKEN),
    ]
    shown = drawn(*stages, root=tmp_path, finished=False)
    expected = "broken doctor; failed lint; skipped format; interrupted test"
    assert shown.endswith(f"check did not pass: {expected}. Logs: {LOGS}")


def test_the_summary_falls_back_to_each_stage_and_the_logs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def failing_drawing(*_arguments: object, **_keywords: object) -> list[str]:
        raise ValueError

    monkeypatch.setattr(loop, "render", failing_drawing)
    record = RunRecord("check", "2026-10-05T00:00:00+00:00", True, [stage()], [])
    assert summary(record, Path(), LOGS) == bare([("lint", FAILED)], "check", LOGS)


def test_the_fallback_still_says_when_every_stage_passed() -> None:
    assert bare([("lint", PASSED)], "check", LOGS)[-1] == f"check passed. Logs: {LOGS}"


def test_the_summary_admits_it_cannot_see_modifications_outside_git(tmp_path: Path) -> None:
    unknown = "Files changed during the run: unknown outside a git repository"
    assert unknown in loop.changes(None, None, tmp_path, tmp_path)


def test_the_summary_lists_a_file_a_fix_reverted() -> None:
    assert changed_files({"src/a.py": "one"}, {}) == ["src/a.py"]


def test_the_summary_names_a_few_changed_files_on_one_line(tmp_path: Path) -> None:
    after = {"src/a.py": "new", "src/b.py": "new"}
    line = "Files changed during the run (2): src/a.py, src/b.py"
    assert line in loop.changes({}, after, tmp_path, tmp_path)


def test_the_summary_points_at_the_full_list_of_many_changed_files(tmp_path: Path) -> None:
    after = {f"src/{n:02}.py": "new" for n in range(12)}
    line = "Files changed during the run (12): all of them: changed.txt"
    assert line in loop.changes({}, after, tmp_path, tmp_path)
    assert (tmp_path / loop.CHANGED).read_text().splitlines() == sorted(after)


def suppression(line: int, label: str) -> Suppression:
    return Suppression("src/a.py", line, label, "# ...")


def test_the_summary_counts_suppressions_with_their_change_and_most_common_rules() -> None:
    present = [
        suppression(1, "noqa E501"),
        suppression(2, "noqa E501"),
        suppression(3, "pyright reportAny"),
    ]
    lines = suppression_lines(Tally(present, 1, present[2:]))
    common = "(most: noqa E501 x2, pyright reportAny x1)"
    headline = f"Suppressions: 3 in the repository, +1 since the last commit {common}"
    assert headline in lines
    assert "Added since the last commit (1):" in lines
    assert "  src/a.py:3  pyright reportAny" in lines


def test_the_summary_says_when_nothing_is_suppressed() -> None:
    lines = suppression_lines(NOTHING_SUPPRESSED)
    assert "Suppressions: none in the repository, unchanged since the last commit" in lines


def test_the_summary_admits_the_change_is_unknown_without_history() -> None:
    lines = suppression_lines(Tally([suppression(1, "noqa E501")], None, None))
    unknown = "Suppressions: 1 in the repository; the change is unknown without a commit to compare"
    assert f"{unknown} (most: noqa E501 x1)" in lines


def test_the_summary_truncates_a_long_list_of_added_suppressions() -> None:
    added = [suppression(number, "noqa E501") for number in range(25)]
    assert "  ... (25 total)" in suppression_lines(Tally(added, 25, added))


# #region Stopping a stage


def test_a_stop_signal_ends_the_stage_and_all_it_started() -> None:
    process = subprocess.Popen(["sleep", "30"], process_group=0)  # noqa: S607
    previous = signal.signal(signal.SIGALRM, loop.interrupted)
    signal.setitimer(signal.ITIMER_REAL, 0.3)
    try:
        with pytest.raises(loop.StopSignalError):
            loop.waited(process, 30)
    finally:
        signal.signal(signal.SIGALRM, previous)
    assert process.returncode == -signal.SIGKILL


def test_a_run_clears_only_files_from_its_folder(tmp_path: Path) -> None:
    (tmp_path / "old.log").write_text("old")
    (tmp_path / "kept").mkdir()
    loop.cleared(tmp_path)
    assert [path.name for path in tmp_path.iterdir()] == ["kept"]
