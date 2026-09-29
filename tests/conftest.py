from pathlib import Path

import pytest

from tests.support import SHARE
from tests.support import Project
from tests.support import manifest


@pytest.fixture
def project(tmp_path: Path) -> Project:
    """A single-unit project wired to this repository's configs, inside a git repository."""
    created = Project(tmp_path / "demo")
    created.write("Makefile", f"include {SHARE / 'harness.mk'}\n")
    created.write("pyproject.toml", manifest())
    created.write("src/demo/__init__.py")
    created.write(".gitignore", "__pycache__/\n")
    created.write("CLAUDE.md", f"@{SHARE / 'agent-rules.md'}\n")
    instructions = created.root / ".github" / "instructions"
    instructions.mkdir(parents=True)
    (instructions / "py-harness.instructions.md").symlink_to(SHARE / "agent-rules.md")
    links = created.root / ".claude" / "skills"
    links.mkdir(parents=True)
    for skill in sorted((SHARE / "skills").iterdir()):
        (links / skill.name).symlink_to(skill)
    created.git("init", "-q")
    return created
