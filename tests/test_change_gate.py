import json
import subprocess
import sys
from pathlib import Path

import pytest

from py_harness.audit import entries
from tests.support import Project

HELD = 2


def hook(root: Path, payload: dict[str, object]) -> subprocess.CompletedProcess[str]:
    command = [sys.executable, "-m", "py_harness.change_gate"]
    event = json.dumps(payload)
    return subprocess.run(
        command, input=event, cwd=root, capture_output=True, text=True, check=False
    )


def call(tool: str, session: str = "s1", **arguments: str) -> dict[str, object]:
    return {"session_id": session, "tool_name": tool, "tool_input": arguments}


def decisions(project: Project) -> list[str]:
    return [entry["decision"] for entry in entries(project.root)]


def test_a_new_file_is_held_once_then_released(project: Project) -> None:
    first = hook(project.root, call("Write", file_path="src/demo/new.py"))
    assert first.returncode == HELD
    assert "before creating src/demo/new.py, tell the user" in first.stderr
    assert hook(project.root, call("Write", file_path="src/demo/new.py")).returncode == 0
    assert decisions(project) == ["held", "released"]


def test_an_edit_to_an_existing_file_passes_unrecorded(project: Project) -> None:
    assert hook(project.root, call("Edit", file_path="src/demo/__init__.py")).returncode == 0
    assert decisions(project) == []


def test_a_destructive_retry_need_not_match_and_a_later_one_asks_again(project: Project) -> None:
    assert hook(project.root, call("Bash", command="rm -f src/demo/x.py")).returncode == HELD
    assert hook(project.root, call("Bash", command="rm  -f  src/demo/x.py")).returncode == 0
    assert hook(project.root, call("Bash", command="rm -f src/demo/y.py")).returncode == HELD


@pytest.mark.parametrize(
    "command",
    [
        "git reset --hard HEAD",
        "git clean -fd",
        "git push --force",
        "git push -f origin main",
        "git checkout -- .",
        "git restore src",
        "git stash drop",
        "git branch -D topic",
        "(cd src && rm x.py)",
    ],
)
def test_each_destructive_command_is_held(project: Project, command: str) -> None:
    assert hook(project.root, call("Bash", command=command)).returncode == HELD


@pytest.mark.parametrize(
    "command", ["git restore --staged src", "git status", "ls -la", "make check"]
)
def test_a_harmless_command_passes(project: Project, command: str) -> None:
    assert hook(project.root, call("Bash", command=command)).returncode == 0


def test_the_wiring_is_held_once_per_session(project: Project) -> None:
    assert hook(project.root, call("Edit", file_path="pyproject.toml")).returncode == HELD
    assert hook(project.root, call("Edit", file_path="pyproject.toml")).returncode == 0
    assert hook(project.root, call("Edit", file_path="pyproject.toml")).returncode == 0
    assert hook(project.root, call("Edit", session="s2", file_path="Makefile")).returncode == HELD


def test_an_editor_patch_is_judged_file_by_file(project: Project) -> None:
    patch = (
        "*** Begin Patch\n*** Add File: src/demo/added.py\n+x = 1\n"
        "*** Update File: src/demo/__init__.py\n*** Delete File: src/demo/gone.py\n"
        "*** Update File: pyproject.toml\n*** End Patch\n"
    )
    assert hook(project.root, call("apply_patch", input=patch)).returncode == HELD
    triggers = [entry["trigger"] for entry in entries(project.root)]
    assert triggers == ["new file", "destructive", "wiring"]


def test_the_editor_agents_tool_names_are_recognised(project: Project) -> None:
    assert hook(project.root, call("create_file", filePath="src/demo/made.py")).returncode == HELD
    assert (
        hook(project.root, call("run_in_terminal", command="rm src/demo/x.py")).returncode == HELD
    )


def test_the_release_keeps_what_the_agent_said_before_the_change(
    project: Project, tmp_path: Path
) -> None:
    transcript = tmp_path / "session.jsonl"
    lines = [
        {"type": "user", "message": {"content": "add a module"}},
        {"type": "assistant", "message": "not an object"},
        {
            "type": "assistant",
            "message": {
                "content": [
                    {"type": "text", "text": "Creating new.py:  the parser needs it."},
                    {"type": "tool_use", "name": "Write"},
                ]
            },
        },
        {"type": "assistant", "message": {"content": "plain"}},
    ]
    transcript.write_text("\n".join(json.dumps(line) for line in lines) + "\n")
    payload = {**call("Write", file_path="src/demo/new.py"), "transcript_path": str(transcript)}
    hook(project.root, payload)
    hook(project.root, payload)
    assert entries(project.root)[-1]["said"] == "Creating new.py: the parser needs it."


def test_outside_git_nothing_is_held(tmp_path: Path) -> None:
    assert hook(tmp_path, call("Write", file_path="new.py")).returncode == 0


def test_an_empty_event_passes(project: Project) -> None:
    command = [sys.executable, "-m", "py_harness.change_gate"]
    completed = subprocess.run(
        command, input="", cwd=project.root, capture_output=True, check=False
    )
    assert completed.returncode == 0


def test_audit_prints_each_decision_and_what_was_said(project: Project, tmp_path: Path) -> None:
    transcript = tmp_path / "session.jsonl"
    said = {"type": "assistant", "message": {"content": [{"type": "text", "text": "Needed."}]}}
    transcript.write_text(json.dumps(said) + "\n")
    payload = {**call("Write", file_path="src/demo/new.py"), "transcript_path": str(transcript)}
    hook(project.root, payload)
    hook(project.root, payload)
    shown = project.make("audit").stdout
    assert "change-gate  held  new file  src/demo/new.py" in shown
    assert "change-gate  released  new file  src/demo/new.py\n    said: Needed." in shown


def test_audit_says_when_nothing_is_recorded(project: Project) -> None:
    assert "audit: nothing recorded in this clone yet" in project.make("audit").stdout
