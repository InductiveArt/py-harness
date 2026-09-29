import glob
import os
import re
from pathlib import Path

from py_harness.console import err
from py_harness.console import out
from py_harness.tables import Table
from py_harness.tables import read_table
from py_harness.tables import string
from py_harness.tables import string_list
from py_harness.tables import subtable
from py_harness.tree import repository_files

IGNORE_FILE = Path(".py-harness") / "ignore"
UNITS_VARIABLE = "PY_HARNESS_UNITS"


class UnitsError(Exception):
    pass


def main() -> int:
    try:
        units = resolve_units(Path.cwd())
    except UnitsError as error:
        err(f"units: {error}")
        return 1
    for unit in units:
        out(unit.as_posix())
    return 0


def resolve_units(root: Path) -> list[Path]:
    """Workspace members in declaration order, then the root when it is a project.

    Raises UnitsError rather than answer with no unit, since a caller handed an
    empty list would iterate nothing and report success having done no work.
    """
    manifest = read_manifest(root)
    declared = workspace_members(root, manifest)
    if "project" in manifest:
        declared.append(Path())
    if not declared:
        raise UnitsError("pyproject.toml declares no [project] and no member with a pyproject.toml")
    ignored = ignored_paths(root)
    kept: list[Path] = []
    for unit in declared:
        if unit.as_posix() in ignored:
            # A skip nobody sees is indistinguishable from coverage.
            err(f"units: '{unit.as_posix()}' excluded by {IGNORE_FILE}")
        else:
            kept.append(unit)
    if not kept:
        raise UnitsError(
            f"every unit is excluded by {IGNORE_FILE}; every stage would pass without running"
        )
    return kept


def units_in_scope(root: Path) -> list[Path]:
    """The units a parent process resolved, so their exclusions are announced once."""
    handed_down = os.environ.get(UNITS_VARIABLE)
    if handed_down is None:
        return resolve_units(root)
    return [Path(line) for line in handed_down.splitlines()]


def encode_units(units: list[Path]) -> str:
    return "\n".join(unit.as_posix() for unit in units)


def project_name(unit: Path) -> str:
    return normalized_name(string(subtable(read_manifest(unit), "project"), "name") or "")


def normalized_name(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def read_manifest(directory: Path) -> Table:
    path = directory / "pyproject.toml"
    if not path.is_file():
        raise UnitsError(f"no pyproject.toml at {directory.resolve()}; nothing can be resolved")
    return read_table(path)


def workspace_members(root: Path, manifest: Table) -> list[Path]:
    workspace = subtable(manifest, "tool", "uv", "workspace")
    excluded = {
        path for pattern in string_list(workspace, "exclude") for path in expand(root, pattern)
    }
    members: list[Path] = []
    for pattern in string_list(workspace, "members"):
        found = [path for path in expand(root, pattern) if path not in excluded]
        if not found:
            err(f"units: workspace member '{pattern}' matches no directory with a pyproject.toml")
        members.extend(found)
    return members


def expand(root: Path, pattern: str) -> list[Path]:
    matches = sorted(glob.glob(pattern, root_dir=root))
    return [Path(match) for match in matches if (root / match / "pyproject.toml").is_file()]


def repository_python_files(root: Path) -> list[Path]:
    """Every Python file below the root, except those of a unit the ignore file lists."""
    ignored = {Path(entry) for entry in ignored_paths(root)}
    units = [*units_in_scope(root), *ignored]
    files = repository_files(root, lambda name: name.endswith(".py"))
    return [path for path in files if owner(path, units) not in ignored]


def owner(path: Path, units: list[Path]) -> Path | None:
    """The deepest unit holding the file, so a member's files never belong to the root."""
    holding = [unit for unit in units if path.is_relative_to(unit)]
    return max(holding, key=lambda unit: len(unit.parts), default=None)


def ignored_paths(root: Path) -> set[str]:
    path = root / IGNORE_FILE
    if not path.is_file():
        return set()
    entries = (line.split("#", 1)[0].strip().rstrip("/") for line in path.read_text().splitlines())
    return {entry for entry in entries if entry}


if __name__ == "__main__":
    raise SystemExit(main())
