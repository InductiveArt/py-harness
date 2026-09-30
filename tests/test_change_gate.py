import json
import subprocess
import sys
from pathlib import Path

import pytest

from py_harness.audit import entries
from tests.support import Project

HELD = 2
PROMPT = "Tidy up: src/demo/old.py is no longer wanted, delete it,\nthen add the parser."
LONG_COMMAND = f"echo {'x' * 300}; rm x.py"
QUOTE = "\u201cdelete it, then add the parser\u201d"


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


def append(transcript: Path, *events: dict[str, object]) -> None:
    with transcript.open("a") as file:
        file.writelines(json.dumps(event) + "\n" for event in events)


def said(words: str) -> dict[str, object]:
    return {"type": "assistant", "message": {"content": [{"type": "text", "text": words}]}}


def typed(words: str) -> dict[str, object]:
    return {"type": "user", "message": {"content": words}}


def block(target: str, asked: str = "") -> str:
    lines = [f"CHANGE: {target}", "WHY: the parser reads it", "UNDO: git rm it"]
    return "\n".join([*lines, f"ASKED: {asked}"] if asked else lines)


@pytest.fixture
def transcript(tmp_path: Path) -> Path:
    path = tmp_path / "session.jsonl"
    append(path, typed(PROMPT))
    return path


def traced(transcript: Path, payload: dict[str, object]) -> dict[str, object]:
    return {**payload, "transcript_path": str(transcript)}


# #region Which changes are held


def test_an_edit_to_an_existing_file_passes_unrecorded(project: Project) -> None:
    assert hook(project.root, call("Edit", file_path="src/demo/__init__.py")).returncode == 0
    assert decisions(project) == []


@pytest.mark.parametrize(
    "command",
    [
        "rm -f src/demo/x.py",
        "git reset --hard HEAD",
        "git clean -fd",
        "git push --force",
        "git push -f origin main",
        "git checkout -- .",
        "git restore src",
        "git stash drop",
        "git branch -D topic",
        "(cd src && rm x.py)",
        pytest.param(LONG_COMMAND, id="past-the-part-a-hold-shows"),
    ],
)
def test_each_destructive_command_is_held(project: Project, command: str) -> None:
    assert hook(project.root, call("Bash", command=command)).returncode == HELD


@pytest.mark.parametrize(
    "command", ["git restore --staged src", "git status", "ls -la", "make check"]
)
def test_a_harmless_command_passes(project: Project, command: str) -> None:
    assert hook(project.root, call("Bash", command=command)).returncode == 0


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


def test_outside_git_nothing_is_held(tmp_path: Path) -> None:
    assert hook(tmp_path, call("Write", file_path="new.py")).returncode == 0


def test_an_empty_event_passes(project: Project) -> None:
    command = [sys.executable, "-m", "py_harness.change_gate"]
    completed = subprocess.run(
        command, input="", cwd=project.root, capture_output=True, check=False
    )
    assert completed.returncode == 0


# #region What a hold asks for


def test_a_hold_prints_the_block_to_write_with_its_change_line_filled_in(
    project: Project,
) -> None:
    stderr = hook(project.root, call("Write", file_path="src/demo/new.py")).stderr
    assert "creating src/demo/new.py is held until the user is told about it." in stderr
    assert "CHANGE: src/demo/new.py\nWHY: <what will use it" in stderr
    assert "UNDO: <the command that reverses it>" in stderr
    assert "as text in your reply, not in your reasoning" in stderr
    assert "ASKED" not in stderr


def test_deleting_or_rewiring_also_asks_for_the_users_own_words(project: Project) -> None:
    stderr = hook(project.root, call("Bash", command="rm src/demo/x.py")).stderr
    assert "CHANGE: rm src/demo/x.py\n" in stderr
    assert "ASKED: <the user's own words asking for it, copied exactly>" in stderr
    assert "If the user never asked for this, do not make it" in stderr


# #region What releases a hold


def test_the_block_written_after_the_hold_releases_the_change(
    project: Project, transcript: Path
) -> None:
    payload = traced(transcript, call("Write", file_path="src/demo/new.py"))
    assert hook(project.root, payload).returncode == HELD
    append(transcript, said(f"Adding the parser module.\n\n{block('src/demo/new.py')}"))
    assert hook(project.root, payload).returncode == 0
    released = entries(project.root)[-1]
    assert (released["why"], released["undo"]) == ("the parser reads it", "git rm it")


def test_a_retry_after_only_reasoning_is_told_reasoning_does_not_count(
    project: Project, transcript: Path
) -> None:
    payload = traced(transcript, call("Write", file_path="src/demo/new.py"))
    hook(project.root, payload)
    reasoning = {"type": "thinking", "thinking": block("src/demo/new.py")}
    append(
        transcript,
        {"type": "assistant", "message": {"content": [reasoning]}},
        {"type": "assistant", "message": "not an object"},
        {"type": "assistant", "message": {}},
    )
    retried = hook(project.root, payload)
    assert retried.returncode == HELD
    assert "is still held: no text since the hold; reasoning does not count." in retried.stderr


def test_a_retry_without_the_block_is_held_again(project: Project, transcript: Path) -> None:
    payload = traced(transcript, call("Write", file_path="src/demo/new.py"))
    hook(project.root, payload)
    append(transcript, said("The gate didn't pick up that note."))
    retried = hook(project.root, payload)
    assert retried.returncode == HELD
    assert "creating src/demo/new.py is still held: no message to the user" in retried.stderr
    append(transcript, {"type": "assistant", "message": {"content": block("src/demo/new.py")}})
    assert hook(project.root, payload).returncode == 0
    assert decisions(project) == ["held", "held", "released"]


def test_a_block_written_before_the_hold_does_not_count(project: Project, transcript: Path) -> None:
    append(transcript, said(block("src/demo/new.py")))
    payload = traced(transcript, call("Write", file_path="src/demo/new.py"))
    hook(project.root, payload)
    assert hook(project.root, payload).returncode == HELD


def test_a_block_for_another_change_does_not_count(project: Project, transcript: Path) -> None:
    payload = traced(transcript, call("Write", file_path="src/demo/new.py"))
    hook(project.root, payload)
    append(transcript, said(block("src/demo/other.py")))
    assert hook(project.root, payload).returncode == HELD


def test_the_field_lines_must_sit_right_under_the_change_line(
    project: Project, transcript: Path
) -> None:
    payload = traced(transcript, call("Write", file_path="src/demo/new.py"))
    hook(project.root, payload)
    append(transcript, said("WHY: early\nCHANGE: src/demo/new.py\n\nWHY: late\nUNDO: late"))
    assert hook(project.root, payload).returncode == HELD
    assert entries(project.root)[-1]["detail"] == "the block leaves WHY and UNDO empty"


def test_a_value_may_be_wrapped_in_backticks(project: Project, transcript: Path) -> None:
    payload = traced(transcript, call("Write", file_path="src/demo/new.py"))
    hook(project.root, payload)
    append(transcript, said(block("`src/demo/new.py`")))
    assert hook(project.root, payload).returncode == 0


def test_a_quote_of_the_users_words_releases_a_deletion(project: Project, transcript: Path) -> None:
    payload = traced(transcript, call("Bash", command="rm src/demo/old.py"))
    hook(project.root, payload)
    append(transcript, said(block("rm src/demo/old.py", asked=QUOTE)))
    assert hook(project.root, payload).returncode == 0
    assert entries(project.root)[-1]["asked"] == "delete it, then add the parser"


def test_a_quote_the_user_never_wrote_is_held(project: Project, transcript: Path) -> None:
    payload = traced(transcript, call("Bash", command="rm src/demo/old.py"))
    hook(project.root, payload)
    skill = {**typed("remove the scratch file"), "isMeta": True}
    result = {"type": "tool_result", "content": "remove the scratch file"}
    append(transcript, skill, {"type": "user", "message": {"content": [result]}})
    append(transcript, said(block("rm src/demo/old.py", asked="remove the scratch file")))
    assert hook(project.root, payload).returncode == HELD
    assert entries(project.root)[-1]["detail"] == "the ASKED words are in no message from the user"


def test_a_quote_under_three_words_is_held(project: Project, transcript: Path) -> None:
    payload = traced(transcript, call("Bash", command="rm src/demo/old.py"))
    hook(project.root, payload)
    append(transcript, said(block("rm src/demo/old.py", asked="delete it")))
    assert hook(project.root, payload).returncode == HELD
    assert entries(project.root)[-1]["detail"] == "ASKED quotes fewer than 3 words"


def test_the_wiring_is_answered_once_per_session(project: Project, transcript: Path) -> None:
    payload = traced(transcript, call("Edit", file_path="pyproject.toml"))
    hook(project.root, payload)
    append(transcript, said(block("pyproject.toml", asked=QUOTE)))
    assert hook(project.root, payload).returncode == 0
    assert hook(project.root, payload).returncode == 0
    assert hook(project.root, call("Edit", session="s2", file_path="Makefile")).returncode == HELD


def test_each_destructive_command_answers_anew(project: Project, transcript: Path) -> None:
    payload = traced(transcript, call("Bash", command="rm src/demo/old.py"))
    hook(project.root, payload)
    append(transcript, said(block("rm src/demo/old.py", asked=QUOTE)))
    assert hook(project.root, payload).returncode == 0
    again = hook(project.root, payload)
    assert again.returncode == HELD
    assert "is held until the user is told about it" in again.stderr


def test_without_a_transcript_the_release_is_marked_unverified(project: Project) -> None:
    hook(project.root, call("Write", file_path="src/demo/new.py"))
    hook(project.root, call("Write", file_path="src/demo/new.py"))
    assert entries(project.root)[-1]["detail"].startswith("unverified")


# #region Audit


def test_audit_prints_each_decision_and_the_block_that_released_it(
    project: Project, transcript: Path
) -> None:
    payload = traced(transcript, call("Bash", command="rm src/demo/old.py"))
    hook(project.root, payload)
    append(transcript, said(block("rm src/demo/old.py", asked=QUOTE)))
    hook(project.root, payload)
    shown = project.make("audit").stdout
    assert "change-gate  held  destructive  rm src/demo/old.py" in shown
    released = "change-gate  released  destructive  rm src/demo/old.py\n"
    fields = "    why: the parser reads it\n    undo: git rm it\n"
    assert released + fields + "    asked: delete it, then add the parser" in shown


def test_audit_says_when_nothing_is_recorded(project: Project) -> None:
    assert "audit: nothing recorded in this clone yet" in project.make("audit").stdout
