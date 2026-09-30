import json
import shutil
import subprocess
from typing import cast

from py_harness.audit import entries
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
