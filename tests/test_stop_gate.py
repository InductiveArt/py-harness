import json
import shutil
import subprocess
from typing import cast

from py_harness.audit import entries
from py_harness.change_gate import Change
from py_harness.change_gate import remember
from py_harness.stop_gate import unshown
from tests.support import SHARE
from tests.support import Project
from tests.support import harness_environment

HELD = 2
LOUD = 'print("hello")\n'


def gate(
    project: Project, event: str = '{"stop_hook_active": false}'
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["uv", "run", "--no-sync", "python", "-m", "py_harness.stop_gate"],  # noqa: S607
        input=event,
        cwd=project.root,
        env=harness_environment(),
        capture_output=True,
        text=True,
        check=False,
    )


def test_the_gate_holds_an_agent_whose_change_fails_the_check(project: Project) -> None:
    project.write("src/demo/loud.py", LOUD)
    result = gate(project)
    assert result.returncode == HELD
    assert "STATUS: FAILED at stage `lint`" in result.stderr
    assert "make check fails. Fix what it names before finishing" in result.stderr


def test_the_gate_releases_a_stop_it_already_held(project: Project) -> None:
    project.write("src/demo/loud.py", LOUD)
    assert gate(project, '{"stop_hook_active": true}').returncode == 0


def test_the_gate_skips_the_check_when_nothing_changed(project: Project) -> None:
    project.write("src/demo/loud.py", LOUD)
    project.commit()
    assert gate(project).returncode == 0


def test_the_gate_lets_a_passing_change_through(project: Project) -> None:
    project.write("tests/test_unit.py", "def test_passes() -> None:\n    assert True\n")
    assert gate(project).returncode == 0


def test_outside_git_the_gate_still_checks(project: Project) -> None:
    shutil.rmtree(project.root / ".git")
    project.write("src/demo/loud.py", LOUD)
    assert gate(project).returncode == HELD


def test_the_gate_reports_a_check_that_cannot_start(project: Project) -> None:
    (project.root / "Makefile").unlink()
    result = gate(project)
    assert result.returncode == HELD
    assert "No rule to make target" in result.stderr


def test_the_shipped_vscode_hook_runs_the_gate() -> None:
    hook = cast("object", json.loads((SHARE / "hooks" / "vscode.json").read_text()))
    assert hook == {
        "hooks": {
            "Stop": [
                {
                    "type": "command",
                    "command": "uv run --no-sync python -m py_harness.stop_gate",
                    "timeout": 900,
                }
            ]
        }
    }


def test_the_gate_records_each_decision_in_the_trail(project: Project) -> None:
    project.write("src/demo/loud.py", LOUD)
    gate(project, '{"session_id": "s1", "stop_hook_active": false}')
    gate(project, '{"session_id": "s1", "stop_hook_active": true}')
    (project.root / "src" / "demo" / "loud.py").unlink()
    project.write("tests/test_unit.py", "def test_passes() -> None:\n    assert True\n")
    gate(project, '{"session_id": "s1", "stop_hook_active": false}')
    assert [entry["decision"] for entry in entries(project.root)] == ["held", "released", "passed"]
    assert "detail: STATUS: FAILED at stage `lint`" in project.make("audit").stdout


# #region Showing the user what the turn changed


def found(changes: list[Change]) -> set[tuple[str, str]]:
    return {(change.trigger, change.target) for change in changes}


def shown(*lines: str) -> str:
    """The gate's request that the client show the user these lines."""
    message = "".join(f"\n  {line}" for line in lines)
    return json.dumps({"systemMessage": f"py-harness, what this turn changed:{message}"}) + "\n"


def test_a_passing_stop_shows_the_user_what_the_turn_changed(project: Project) -> None:
    remember(project.root, "s1")
    project.write("tests/test_unit.py", "def test_passes() -> None:\n    assert True\n")
    result = gate(project, '{"session_id": "s1", "stop_hook_active": false}')
    assert result.returncode == 0
    assert result.stdout == shown("created tests/test_unit.py")


def test_the_stop_that_ends_a_held_turn_shows_it_too(project: Project) -> None:
    remember(project.root, "s1")
    project.write("src/demo/loud.py", LOUD)
    result = gate(project, '{"session_id": "s1", "stop_hook_active": true}')
    assert result.stdout == shown("created src/demo/loud.py")


def test_a_held_stop_shows_nothing_yet(project: Project) -> None:
    remember(project.root, "s1")
    project.write("src/demo/loud.py", LOUD)
    result = gate(project, '{"session_id": "s1", "stop_hook_active": false}')
    assert result.returncode == HELD
    assert result.stdout == ""


def test_a_change_is_shown_once_until_it_changes_again(project: Project) -> None:
    project.commit()
    remember(project.root, "s1")
    project.write("pyproject.toml", project.read("pyproject.toml") + "\n")
    assert found(unshown(project.root, "s1")) == {("wiring", "pyproject.toml")}
    assert unshown(project.root, "s1") == []
    project.write("pyproject.toml", project.read("pyproject.toml") + "\n")
    assert found(unshown(project.root, "s1")) == {("wiring", "pyproject.toml")}


def test_every_kind_of_change_is_shown(project: Project) -> None:
    project.write("src/demo/gone.py")
    project.write("src/demo/removed.py")
    project.commit()
    remember(project.root, "s1")
    project.git("rm", "-q", "src/demo/gone.py")
    (project.root / "src/demo/removed.py").unlink()
    project.write("src/demo/made.py")
    project.write("src/demo/staged.py")
    project.git("add", "src/demo/staged.py")
    project.write("pyproject.toml", project.read("pyproject.toml") + "\n")
    project.write("src/demo/__init__.py", "VALUE = 1\n")
    assert found(unshown(project.root, "s1")) == {
        ("destructive", "src/demo/gone.py"),
        ("destructive", "src/demo/removed.py"),
        ("new file", "src/demo/made.py"),
        ("new file", "src/demo/staged.py"),
        ("wiring", "pyproject.toml"),
    }


def test_what_the_tree_held_when_the_session_began_is_not_shown(project: Project) -> None:
    project.write("notes.txt", "mine\n")
    remember(project.root, "s1")
    assert unshown(project.root, "s1") == []


def test_editing_a_file_the_session_found_untracked_is_not_creating_it(project: Project) -> None:
    project.write("src/demo/draft.py")
    remember(project.root, "s1")
    project.write("src/demo/draft.py", "VALUE = 1\n")
    assert unshown(project.root, "s1") == []


def test_without_a_baseline_nothing_is_shown(project: Project) -> None:
    project.write("src/demo/made.py")
    assert unshown(project.root, "s1") == []


def test_an_unreadable_index_shows_nothing(project: Project) -> None:
    remember(project.root, "s1")
    project.write("src/demo/made.py")
    (project.root / ".git" / "index").write_text("not an index")
    assert unshown(project.root, "s1") == []
    remember(project.root, "s2")
    assert not (project.root / ".git" / "py-harness" / "baselines" / "s2.json").exists()
