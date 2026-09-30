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
    created.git("init", "-q")
    return created
