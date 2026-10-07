from pathlib import Path

from py_harness.baseline import recorded_line
from py_harness.suppressions import BASELINE
from py_harness.suppressions import read_baseline
from py_harness.suppressions import recorded
from py_harness.suppressions import recorded_errors
from py_harness.verdict import FAILED
from py_harness.verdict import PASSED
from py_harness.verdict import VERDICT_VARIABLE
from py_harness.verdict import Verdict
from py_harness.verdict import read_verdict
from tests.support import Project

ONE_ERROR = 'COUNT: int = "none"\n'


def typecheck(project: Project, folder: Path) -> Verdict | None:
    target = folder / "typecheck.verdict"
    project.make("typecheck", environment={VERDICT_VARIABLE: str(target)})
    return read_verdict(target)


def recorded_and_committed(project: Project) -> None:
    project.write("src/demo/old.py", ONE_ERROR)
    project.commit()
    project.make("baseline")
    project.commit()


def test_recording_says_how_many_errors_the_change_added(project: Project) -> None:
    project.write("src/demo/old.py", ONE_ERROR)
    project.commit()
    result = project.make("baseline")
    assert result.returncode == 0
    assert result.stdout.endswith("baseline: 1 recorded (+1 since the last commit)\n")


def test_a_recorded_error_no_longer_fails(project: Project, tmp_path: Path) -> None:
    recorded_and_committed(project)
    assert typecheck(project, tmp_path) == Verdict(PASSED, "1 recorded")


def test_a_new_error_fails_beside_the_recorded_ones(project: Project, tmp_path: Path) -> None:
    recorded_and_committed(project)
    project.write("src/demo/new.py", ONE_ERROR)
    verdict = typecheck(project, tmp_path)
    assert verdict is not None
    assert verdict.outcome == FAILED
    assert verdict.headline == "1 error in 1 file; 1 recorded"


def test_fixing_a_recorded_error_shrinks_the_baseline(project: Project, tmp_path: Path) -> None:
    recorded_and_committed(project)
    project.write("src/demo/old.py", "COUNT: int = 0\n")
    assert typecheck(project, tmp_path) == Verdict(PASSED, "0 recorded")
    assert recorded_errors(read_baseline(project.root)) == []
    held = recorded(project.root)
    assert held is not None
    assert held.change == -1


def test_code_without_a_type_error_records_nothing(project: Project) -> None:
    result = project.make("baseline")
    assert result.stdout.endswith("baseline: nothing to record, the code has no type error\n")
    assert not (project.root / BASELINE).exists()


def test_nothing_is_recorded_while_a_config_misses_the_harness(project: Project) -> None:
    project.write("src/demo/old.py", ONE_ERROR)
    project.write("pyrightconfig.json", "{}\n")
    result = project.make("baseline")
    assert result.returncode != 0
    assert "baseline: nothing recorded" in result.stderr
    assert not (project.root / BASELINE).exists()


def test_the_count_is_every_entry_the_baseline_holds(tmp_path: Path) -> None:
    fresh = Project(tmp_path)
    fresh.git("init", "-q")
    fresh.write(BASELINE.as_posix(), '{"files": {"./a.py": [{}, {}]}}')
    assert recorded_line(tmp_path) == "2 recorded"


def test_without_a_baseline_there_is_no_count(tmp_path: Path) -> None:
    assert recorded_line(tmp_path) == ""


def test_a_baseline_that_cannot_be_written_fails_with_the_checkers_code(project: Project) -> None:
    project.write("src/demo/old.py", ONE_ERROR)
    project.write(BASELINE.parent.as_posix(), "a file where the baseline's folder belongs\n")
    result = project.make("baseline")
    assert result.returncode != 0
    assert "baseline:" not in result.stdout
