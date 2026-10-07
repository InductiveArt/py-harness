import json
import subprocess
import sys
from pathlib import Path

import pytest

from py_harness.audit import entries
from tests.support import QUOTE
from tests.support import Project
from tests.support import append
from tests.support import typed

HELD = 2


def hook(root: Path, payload: dict[str, object]) -> subprocess.CompletedProcess[str]:
    command = [sys.executable, "-m", "py_harness.change_gate"]
    event = json.dumps(payload)
    return subprocess.run(
        command, input=event, cwd=root, capture_output=True, text=True, check=False
    )


def call(tool: str, session: str = "s1", **arguments: str) -> dict[str, object]:
    return {"session_id": session, "tool_name": tool, "tool_input": arguments}


def traced(transcript: Path, payload: dict[str, object]) -> dict[str, object]:
    return {**payload, "transcript_path": str(transcript)}


def removal(transcript: Path, description: str) -> dict[str, object]:
    return traced(transcript, call("Bash", command="rm src/demo/old.py", description=description))


def decisions(project: Project) -> list[str]:
    return [entry["decision"] for entry in entries(project.root)]


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
        pytest.param(f"echo {'x' * 300}; rm x.py", id="past-the-part-a-hold-shows"),
    ],
)
def test_each_destructive_command_is_held(project: Project, command: str) -> None:
    assert hook(project.root, call("Bash", command=command)).returncode == HELD


@pytest.mark.parametrize(
    "command", ["git restore --staged src", "git status", "ls -la", "make fix-check"]
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


# #region A new file or a wiring edit


def test_a_new_file_is_held_once_to_be_thought_through(project: Project) -> None:
    first = hook(project.root, call("Write", file_path="src/demo/new.py"))
    assert first.returncode == HELD
    assert "before creating src/demo/new.py, present these facts:" in first.stderr
    assert "which existing module could hold it instead: search for one" in first.stderr
    assert hook(project.root, call("Write", file_path="src/demo/new.py")).returncode == 0
    assert hook(project.root, call("Write", file_path="src/demo/new.py")).returncode == 0
    assert decisions(project) == ["held", "released"]


def test_the_wiring_is_held_once_per_session(project: Project) -> None:
    first = hook(project.root, call("Edit", file_path="pyproject.toml"))
    assert first.returncode == HELD
    assert "the user's words asking for it. Then make the same change again." in first.stderr
    assert hook(project.root, call("Edit", file_path="pyproject.toml")).returncode == 0
    assert hook(project.root, call("Edit", session="s2", file_path="Makefile")).returncode == HELD


# #region A destructive command


def test_a_command_whose_description_quotes_the_user_runs_at_once(
    project: Project, transcript: Path
) -> None:
    payload = removal(transcript, f"Delete old.py, as you asked: {QUOTE}")
    assert hook(project.root, payload).returncode == 0
    assert decisions(project) == ["released"]
    assert entries(project.root)[-1]["asked"] == "delete it, then add the parser"


def test_a_command_without_the_users_words_is_held(project: Project, transcript: Path) -> None:
    held = hook(project.root, removal(transcript, "Delete old.py"))
    assert held.returncode == HELD
    reason = "running `rm src/demo/old.py` is held: its description quotes nothing the user said."
    assert reason in held.stderr
    assert "If they never asked, do not" in held.stderr
    assert "If git cannot restore what it deletes,\nkeep a copy first" in held.stderr


def test_a_quote_the_user_never_wrote_is_held(project: Project, transcript: Path) -> None:
    skill = {**typed("remove the scratch file"), "isMeta": True}
    result = {"type": "tool_result", "content": "remove the scratch file"}
    ran = {"type": "user", "message": {"content": [result]}}
    append(
        transcript,
        skill,
        {**ran, "toolUseResult": "remove the scratch file"},
        {**ran, "toolUseResult": {"stdout": "remove the scratch file"}},
        {"type": "user", "message": {}},
    )
    payload = removal(transcript, 'Delete old.py: "remove the scratch file"')
    assert hook(project.root, payload).returncode == HELD


def test_the_users_words_in_a_message_of_blocks_count(project: Project, transcript: Path) -> None:
    pasted = [{"type": "text", "text": "Please remove the old module now."}]
    append(transcript, {"type": "user", "message": {"content": pasted}})
    payload = removal(transcript, 'Delete old.py: "remove the old module"')
    assert hook(project.root, payload).returncode == 0


def test_a_quote_under_three_words_is_held(project: Project, transcript: Path) -> None:
    assert hook(project.root, removal(transcript, 'Delete old.py: "delete it"')).returncode == HELD


def test_an_answer_picked_from_the_agents_question_counts_as_the_users_words(
    project: Project, transcript: Path
) -> None:
    answers = {"answers": {"What about old.py?": "Delete it, it is dead", "Why?": 1}}
    result = {"type": "tool_result", "content": "The user answered."}
    append(transcript, {"type": "user", "message": {"content": [result]}, "toolUseResult": answers})
    payload = removal(transcript, 'Delete old.py: "Delete it, it is dead"')
    assert hook(project.root, payload).returncode == 0


def test_the_editor_agents_explanation_carries_the_quote(
    project: Project, transcript: Path
) -> None:
    command = call("run_in_terminal", command="rm src/demo/old.py", explanation=QUOTE)
    assert hook(project.root, traced(transcript, command)).returncode == 0


def test_each_destructive_command_answers_anew(project: Project, transcript: Path) -> None:
    assert hook(project.root, removal(transcript, QUOTE)).returncode == 0
    assert hook(project.root, removal(transcript, "Delete it again")).returncode == HELD


def test_without_a_transcript_a_destructive_command_is_held_once_then_marked_unverified(
    project: Project,
) -> None:
    first = hook(project.root, call("Bash", command="rm src/demo/old.py"))
    assert "is held: no transcript shows what the user said." in first.stderr
    assert hook(project.root, call("Bash", command="rm src/demo/old.py")).returncode == 0
    assert entries(project.root)[-1]["detail"].startswith("unverified")


# #region The tree the session started from


def test_the_first_gated_call_keeps_the_tree_as_the_session_found_it(project: Project) -> None:
    hook(project.root, call("Bash", command="ls"))
    project.write("src/demo/later.py")
    hook(project.root, call("Bash", command="ls"))
    kept = (project.root / ".git" / "py-harness" / "starting-trees" / "s1.json").read_text()
    assert '"src/demo/__init__.py"' in kept
    assert "later.py" not in kept


# #region Audit


def test_audit_prints_each_decision_and_the_users_words(project: Project, transcript: Path) -> None:
    hook(project.root, removal(transcript, "Delete old.py"))
    hook(project.root, removal(transcript, QUOTE))
    shown = project.make("audit").stdout
    assert "change-gate  held  destructive  rm src/demo/old.py" in shown
    released = "change-gate  released  destructive  rm src/demo/old.py\n"
    assert released + "    asked: delete it, then add the parser" in shown


def test_audit_says_when_nothing_is_recorded(project: Project) -> None:
    assert "audit: nothing recorded in this clone yet" in project.make("audit").stdout
