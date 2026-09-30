import shutil
import subprocess
from pathlib import Path

import pytest

from py_harness import agent
from py_harness.loop import LOOPS
from tests.support import SHARE
from tests.support import Project

LINKS = {
    ".claude/rules/py-harness.md": SHARE / "agent-rules.md",
    ".claude/skills/py-commenting": SHARE / "skills" / "py-commenting",
    ".claude/skills/py-quality-tooling": SHARE / "skills" / "py-quality-tooling",
}


def excluded(project: Project) -> str:
    return (project.root / ".git" / "info" / "exclude").read_text()


def untracked(project: Project) -> str:
    git = shutil.which("git")
    assert git is not None
    command = [git, "status", "--porcelain", "--untracked-files=all"]
    return subprocess.run(
        command, cwd=project.root, capture_output=True, text=True, check=True
    ).stdout


def test_agent_links_the_rules_and_every_skill_into_the_harness(project: Project) -> None:
    assert project.make("agent").returncode == 0
    for link, target in LINKS.items():
        path = project.root / link
        assert path.is_symlink()
        assert path.resolve() == target.resolve()


def test_agent_keeps_its_links_out_of_git(project: Project) -> None:
    project.make("agent")
    assert ".claude" not in untracked(project)
    for link in LINKS:
        assert f"/{link}\n" in excluded(project)


def test_agent_changes_nothing_on_a_second_run(project: Project) -> None:
    project.make("agent")
    before = excluded(project)
    again = project.make("agent")
    assert again.stdout.strip() == "\u2192 agent"
    assert excluded(project) == before


def test_agent_leaves_anything_that_is_not_a_link_and_fails(project: Project) -> None:
    project.write(".claude/skills/py-commenting/SKILL.md", "mine\n")
    blocked = project.make("agent")
    assert blocked.returncode != 0
    assert "agent: .claude/skills/py-commenting is not a link" in blocked.stderr
    assert project.read(".claude/skills/py-commenting/SKILL.md") == "mine\n"
    assert "/.claude/skills/py-commenting\n" not in excluded(project)
    shutil.rmtree(project.root / ".claude" / "skills" / "py-commenting")
    assert project.make("agent").returncode == 0
    assert excluded(project).count(agent.EXCLUDE_HEADING) == 1
    assert "/.claude/skills/py-commenting\n" in excluded(project)


def test_agent_replaces_a_link_to_anything_else(project: Project) -> None:
    link = project.root / ".claude" / "skills" / "py-commenting"
    link.parent.mkdir(parents=True)
    link.symlink_to(project.root / "src")
    project.make("agent")
    assert link.resolve() == LINKS[".claude/skills/py-commenting"].resolve()


def test_agent_outside_git_still_links(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    assert agent.main(["agent", str(SHARE)]) == 0
    assert (tmp_path / ".claude" / "rules" / "py-harness.md").is_symlink()


def test_check_and_ready_link_the_agent_layer_and_ci_never_does() -> None:
    assert LOOPS["check"][0] == "agent"
    assert LOOPS["ready"][:2] == ("install", "agent")
    assert "agent" not in LOOPS["ci"]
