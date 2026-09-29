from pathlib import Path

from py_harness.tables import string
from py_harness.tables import string_list
from py_harness.tables import subtable
from py_harness.units import read_manifest

DEFAULT_SOURCE = "src"
DEFAULT_TESTS = "tests"


def source_root(unit: Path) -> Path:
    """Where a unit's code lives: its `[tool.py-harness] source`, or `src`."""
    settings = subtable(read_manifest(unit), "tool", "py-harness")
    return unit / (string(settings, "source") or DEFAULT_SOURCE)


def source_packages(unit: Path) -> list[str]:
    return sorted(marker.parent.name for marker in source_root(unit).glob("*/__init__.py"))


def suite_patterns(unit: Path) -> list[str]:
    """Where a unit's tests live, as its own pytest configuration declares, or `tests`."""
    settings = subtable(read_manifest(unit), "tool", "pytest", "ini_options")
    return string_list(settings, "testpaths") or [DEFAULT_TESTS]


def suite_paths(unit: Path) -> list[Path]:
    return sorted({match for pattern in suite_patterns(unit) for match in unit.glob(pattern)})
