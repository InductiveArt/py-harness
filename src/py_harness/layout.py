from pathlib import Path

from py_harness.projects import read_manifest
from py_harness.tables import string
from py_harness.tables import string_list
from py_harness.tables import subtable

DEFAULT_SOURCE = "src"
DEFAULT_TESTS = "tests"


def source_root(project: Path) -> Path:
    """Where a project's code lives: its `[tool.py-harness] source`, or `src`."""
    settings = subtable(read_manifest(project), "tool", "py-harness")
    return project / (string(settings, "source") or DEFAULT_SOURCE)


def source_packages(project: Path) -> list[str]:
    return sorted(marker.parent.name for marker in source_root(project).glob("*/__init__.py"))


def suite_patterns(project: Path) -> list[str]:
    """Where a project's tests live, as its own pytest configuration declares, or `tests`."""
    settings = subtable(read_manifest(project), "tool", "pytest", "ini_options")
    return string_list(settings, "testpaths") or [DEFAULT_TESTS]


def suite_paths(project: Path) -> list[Path]:
    return sorted({match for pattern in suite_patterns(project) for match in project.glob(pattern)})
