import json
import shutil
import subprocess
from pathlib import Path
from typing import cast

import pytest

from py_harness.audit import entries
from py_harness.change_gate import Change
from py_harness.change_gate import remember
from py_harness.stop_gate import untold
from tests.support import QUOTE
from tests.support import SHARE
from tests.support import Project
from tests.support import append
from tests.support import harness_environment
from tests.support import said

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


def test_an_untold_change_holds_the_stop_even_when_the_check_passes(project: Project) -> None:
    remember(project.root, "s1")
    project.write("tests/test_unit.py", "def test_passes() -> None:\n    assert True\n")
    result = gate(project, '{"session_id": "s1", "stop_hook_active": false}')
    assert result.returncode == HELD
    assert "creating tests/test_unit.py was done, but" in result.stderr
    held = entries(project.root)[-1]
    assert (held["hook"], held["decision"]) == ("stop-gate", "held")
    assert held["detail"] == "changes no block told the user about"


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


# #region Telling the user at the end of the turn


def stop(transcript: Path, session: str = "s1") -> dict[str, object]:
    return {"session_id": session, "transcript_path": str(transcript)}


def found(changes: list[Change]) -> set[tuple[str, str]]:
    return {(change.trigger, change.target) for change in changes}


def block(target: str, asked: str = "") -> str:
    lines = [f"CHANGE: {target}", "WHY: the parser reads it", "UNDO: git rm it"]
    return "\n".join([*lines, f"ASKED: {asked}"] if asked else lines)


def made(project: Project, relative: str = "src/demo/made.py") -> None:
    """A change the session makes after its baseline, as a shell command would."""
    remember(project.root, "s1")
    project.write(relative)


def test_a_file_a_command_made_is_held_at_the_end_of_the_turn(
    project: Project, transcript: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    made(project)
    held = untold(project.root, stop(transcript), final=False)
    assert found(held) == {("new file", "src/demo/made.py")}
    stderr = capsys.readouterr().err
    assert "creating src/demo/made.py was done, but no text reached the user" in stderr
    assert "CHANGE: src/demo/made.py\nWHY: <what will use it" in stderr


def test_the_end_of_the_turn_finds_every_kind_of_change(project: Project, transcript: Path) -> None:
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
    assert found(untold(project.root, stop(transcript), final=False)) == {
        ("destructive", "src/demo/gone.py"),
        ("destructive", "src/demo/removed.py"),
        ("new file", "src/demo/made.py"),
        ("new file", "src/demo/staged.py"),
        ("wiring", "pyproject.toml"),
    }


def test_what_the_tree_held_when_the_session_began_is_not_held(
    project: Project, transcript: Path
) -> None:
    project.write("notes.txt", "mine\n")
    remember(project.root, "s1")
    assert untold(project.root, stop(transcript), final=False) == []


def test_without_a_baseline_the_end_of_the_turn_judges_nothing(
    project: Project, transcript: Path
) -> None:
    project.write("src/demo/made.py")
    assert untold(project.root, stop(transcript), final=False) == []


def test_an_unreadable_index_judges_nothing(project: Project, transcript: Path) -> None:
    made(project)
    (project.root / ".git" / "index").write_text("not an index")
    assert untold(project.root, stop(transcript), final=False) == []
    remember(project.root, "s2")
    assert not (project.root / ".git" / "py-harness" / "baselines" / "s2.json").exists()


def test_a_block_in_the_closing_message_releases_the_change(
    project: Project, transcript: Path
) -> None:
    made(project)
    untold(project.root, stop(transcript), final=False)
    append(transcript, said(f"Done.\n\n{block('src/demo/made.py')}"))
    assert untold(project.root, stop(transcript), final=True) == []
    assert entries(project.root)[-1]["why"] == "the parser reads it"


def test_the_closing_message_counts_before_the_transcript_holds_it(
    project: Project, transcript: Path
) -> None:
    made(project)
    closing = {**stop(transcript), "last_assistant_message": block("src/demo/made.py")}
    assert untold(project.root, closing, final=False) == []


def test_a_block_written_earlier_in_the_session_counts(project: Project, transcript: Path) -> None:
    made(project)
    append(transcript, said(block("src/demo/made.py")))
    assert untold(project.root, stop(transcript), final=False) == []


def test_a_change_told_once_is_not_judged_again(project: Project, transcript: Path) -> None:
    made(project)
    append(transcript, said(block("src/demo/made.py")))
    untold(project.root, stop(transcript), final=False)
    recorded = entries(project.root)
    project.write("src/demo/made.py", "VALUE = 2\n")
    assert untold(project.root, stop(transcript), final=False) == []
    assert entries(project.root) == recorded


def test_editing_a_file_the_session_found_untracked_is_not_creating_it(
    project: Project, transcript: Path
) -> None:
    project.write("src/demo/draft.py")
    remember(project.root, "s1")
    project.write("src/demo/draft.py", "VALUE = 1\n")
    assert untold(project.root, stop(transcript), final=False) == []


def test_reasoning_does_not_tell_the_user(project: Project, transcript: Path) -> None:
    made(project)
    reasoning = {"type": "thinking", "thinking": block("src/demo/made.py")}
    append(
        transcript,
        {"type": "assistant", "message": {"content": [reasoning]}},
        {"type": "assistant", "message": "not an object"},
        {"type": "assistant", "message": {}},
    )
    assert len(untold(project.root, stop(transcript), final=False)) == 1
    assert entries(project.root)[-1]["detail"] == (
        "no text reached the user; reasoning does not count"
    )


def test_a_block_for_another_change_does_not_count(project: Project, transcript: Path) -> None:
    made(project)
    append(transcript, said(block("src/demo/other.py")))
    assert len(untold(project.root, stop(transcript), final=False)) == 1


def test_the_field_lines_must_sit_right_under_the_change_line(
    project: Project, transcript: Path
) -> None:
    made(project)
    append(transcript, said("WHY: early\nCHANGE: src/demo/made.py\n\nWHY: late\nUNDO: late"))
    untold(project.root, stop(transcript), final=False)
    assert entries(project.root)[-1]["detail"] == "the block leaves WHY and UNDO empty"


def test_a_value_may_be_wrapped_in_backticks(project: Project, transcript: Path) -> None:
    made(project)
    append(transcript, said(block("`src/demo/made.py`")))
    assert untold(project.root, stop(transcript), final=False) == []


def test_only_marks_around_the_whole_value_are_dropped(project: Project, transcript: Path) -> None:
    made(project)
    append(transcript, said("CHANGE: src/demo/made.py\nWHY: `slugify` needs a home\nUNDO: x"))
    untold(project.root, stop(transcript), final=False)
    assert entries(project.root)[-1]["why"] == "`slugify` needs a home"


def test_a_config_edit_is_told_with_the_users_own_words(project: Project, transcript: Path) -> None:
    remember(project.root, "s1")
    project.write("pyproject.toml", project.read("pyproject.toml") + "\n")
    append(transcript, said(block("pyproject.toml", asked="rewire the build")))
    untold(project.root, stop(transcript), final=False)
    assert entries(project.root)[-1]["detail"] == "its ASKED line: the user said none of it"
    append(transcript, said(block("pyproject.toml", asked=QUOTE)))
    assert untold(project.root, stop(transcript), final=False) == []
    assert entries(project.root)[-1]["asked"] == "delete it, then add the parser"


def test_the_final_stop_releases_what_is_still_untold_and_marks_it(
    project: Project, transcript: Path
) -> None:
    made(project)
    untold(project.root, stop(transcript), final=False)
    assert untold(project.root, stop(transcript), final=True) == []
    released = entries(project.root)[-1]
    assert (released["decision"], released["detail"]) == (
        "released",
        "untold: no text reached the user; reasoning does not count",
    )


def test_without_a_transcript_the_end_of_the_turn_holds_once(project: Project) -> None:
    made(project)
    assert len(untold(project.root, {"session_id": "s1"}, final=False)) == 1
    assert untold(project.root, {"session_id": "s1"}, final=True) == []
    assert entries(project.root)[-1]["detail"].startswith("unverified")
