from pathlib import Path

import pytest

from py_harness.units import UNITS_VARIABLE
from py_harness.units import UnitsError
from py_harness.units import repository_python_files
from py_harness.units import resolve_units
from py_harness.units import units_in_scope
from tests.support import Project


def workspace(
    root: Path, members: list[str], *, exclude: list[str] | None = None, project: bool = False
) -> Project:
    created = Project(root)
    table = '[project]\nname = "root"\n\n' if project else ""
    excluded = "" if exclude is None else f"exclude = {exclude!r}\n"
    created.write(
        "pyproject.toml", f"{table}[tool.uv.workspace]\nmembers = {members!r}\n{excluded}"
    )
    return created


def member(created: Project, path: str) -> None:
    created.write(f"{path}/pyproject.toml", f'[project]\nname = "{Path(path).name}"\n')


def test_a_single_project_is_one_unit(tmp_path: Path) -> None:
    Project(tmp_path).write("pyproject.toml", '[project]\nname = "solo"\n')
    assert resolve_units(tmp_path) == [Path()]


def test_members_resolve_in_declaration_order_with_globs_expanded(tmp_path: Path) -> None:
    created = workspace(tmp_path, ["tools/zeta", "libs/*"])
    for path in ("libs/b", "libs/a", "tools/zeta"):
        member(created, path)
    assert resolve_units(tmp_path) == [Path("tools/zeta"), Path("libs/a"), Path("libs/b")]


def test_a_root_project_follows_its_members(tmp_path: Path) -> None:
    created = workspace(tmp_path, ["libs/*"], project=True)
    member(created, "libs/a")
    assert resolve_units(tmp_path) == [Path("libs/a"), Path()]


def test_a_workspace_exclude_removes_a_member(tmp_path: Path) -> None:
    created = workspace(tmp_path, ["libs/*"], exclude=["libs/b"])
    member(created, "libs/a")
    member(created, "libs/b")
    assert resolve_units(tmp_path) == [Path("libs/a")]


def test_a_member_matching_nothing_is_announced(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    created = workspace(tmp_path, ["libs/*", "missing"])
    member(created, "libs/a")
    assert resolve_units(tmp_path) == [Path("libs/a")]
    assert "workspace member 'missing' matches no directory" in capsys.readouterr().err


def test_an_ignored_unit_is_dropped_and_announced(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    created = workspace(tmp_path, ["libs/*"])
    member(created, "libs/a")
    member(created, "libs/b")
    created.write(".py-harness/ignore", "# retired\nlibs/b/  # kept for reference\n")
    assert resolve_units(tmp_path) == [Path("libs/a")]
    assert "'libs/b' excluded by .py-harness/ignore" in capsys.readouterr().err


def python_files_of(created: Project) -> list[str]:
    for path in ("src/root.py", "libs/a/src/a.py", "libs/b/src/b.py", "scripts/tool.py"):
        created.write(path)
    return [path.as_posix() for path in repository_python_files(created.root)]


def test_the_repository_python_files_leave_out_an_ignored_member(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv(UNITS_VARIABLE, raising=False)
    created = workspace(tmp_path, ["libs/*"], project=True)
    member(created, "libs/a")
    member(created, "libs/b")
    created.write(".py-harness/ignore", "libs/b\n")
    assert python_files_of(created) == ["libs/a/src/a.py", "scripts/tool.py", "src/root.py"]


def test_an_ignored_root_keeps_its_members_python_files(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv(UNITS_VARIABLE, raising=False)
    created = workspace(tmp_path, ["libs/*"], project=True)
    member(created, "libs/a")
    member(created, "libs/b")
    created.write(".py-harness/ignore", ".\n")
    assert python_files_of(created) == ["libs/a/src/a.py", "libs/b/src/b.py"]


def test_a_python_file_outside_every_unit_is_kept(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv(UNITS_VARIABLE, raising=False)
    created = workspace(tmp_path, ["libs/*"])
    member(created, "libs/a")
    member(created, "libs/b")
    assert "scripts/tool.py" in python_files_of(created)


def test_every_unit_ignored_is_an_error(tmp_path: Path) -> None:
    created = Project(tmp_path)
    created.write("pyproject.toml", '[project]\nname = "solo"\n')
    created.write(".py-harness/ignore", ".\n")
    with pytest.raises(UnitsError, match="every stage would pass without running"):
        resolve_units(tmp_path)


def test_a_missing_manifest_is_an_error(tmp_path: Path) -> None:
    with pytest.raises(UnitsError, match=r"no pyproject\.toml"):
        resolve_units(tmp_path)


def test_a_manifest_with_neither_project_nor_members_is_an_error(tmp_path: Path) -> None:
    workspace(tmp_path, ["libs/*"])
    with pytest.raises(UnitsError, match="declares no \\[project\\]"):
        resolve_units(tmp_path)


def test_handed_down_units_are_read_without_resolving(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(UNITS_VARIABLE, "libs/a\nlibs/b")
    assert units_in_scope(tmp_path) == [Path("libs/a"), Path("libs/b")]


def test_units_resolve_when_nothing_is_handed_down(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv(UNITS_VARIABLE, raising=False)
    Project(tmp_path).write("pyproject.toml", '[project]\nname = "solo"\n')
    assert units_in_scope(tmp_path) == [Path()]
