from pathlib import Path

import pytest

from tests.support import PROMPT
from tests.support import SHARE
from tests.support import Project
from tests.support import append
from tests.support import manifest
from tests.support import typed


@pytest.fixture
def project(tmp_path: Path) -> Project:
    """A single-unit project wired to this repository's configs, inside a git repository."""
    created = Project(tmp_path / "demo")
    created.write("Makefile", f"include {SHARE / 'harness.mk'}\n")
    created.write("pyproject.toml", manifest())
    created.write("src/demo/__init__.py")
    created.write(".gitignore", "__pycache__/\n")
    created.git("init", "-q")
    return created


@pytest.fixture
def transcript(tmp_path: Path) -> Path:
    """An agent session's transcript, holding the user's prompt."""
    path = tmp_path / "session.jsonl"
    append(path, typed(PROMPT))
    return path
