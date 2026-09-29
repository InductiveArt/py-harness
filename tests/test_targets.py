import pytest

from tests.support import Project
from tests.support import declared_targets

LEFTOVERS = ("dist/demo.whl", ".ruff_cache/x", ".pytest_cache/x")
REPO_WIDE_STAGES = (
    "format",
    "format-fix",
    "lint",
    "lint-fix",
    "lint-fix-unsafe",
    "typecheck",
    "doctor",
)


@pytest.mark.parametrize("target", REPO_WIDE_STAGES)
def test_a_repo_wide_stage_opens_with_its_name(project: Project, target: str) -> None:
    assert project.make(target).stdout.startswith(f"\n→ {target}\n")


def test_help_lists_every_declared_target(project: Project) -> None:
    shown = project.make("help").stdout
    missing = [name for name in declared_targets() if name != "help" and f"  {name} " not in shown]
    assert missing == []


def test_units_prints_every_unit(project: Project) -> None:
    result = project.make("units")
    assert result.returncode == 0
    assert result.stdout == ".\n"


def test_units_reports_what_it_cannot_resolve(project: Project) -> None:
    project.write("pyproject.toml", '[tool.uv.workspace]\nmembers = ["libs/*"]\n')
    result = project.make("units")
    assert result.returncode != 0
    assert "units: pyproject.toml declares no [project]" in result.stderr


def test_clean_removes_build_output_and_caches(project: Project) -> None:
    for leftover in LEFTOVERS:
        project.write(leftover)
    assert project.make("clean").returncode == 0
    assert [name for name in LEFTOVERS if (project.root / name).exists()] == []
