import os
from collections.abc import Callable
from pathlib import Path

NEVER_ENTERED = frozenset({"__pycache__", "node_modules"})


def repository_files(
    root: Path, keep: Callable[[str], bool], skipped: Path | None = None
) -> list[Path]:
    """Files below the root whose names pass `keep`, relative to the root."""
    excluded = None if skipped is None else skipped.resolve()
    found: list[Path] = []
    for directory, subdirectories, names in os.walk(root):
        # Hidden directories hold tool state and environments, never the repository's own files.
        subdirectories[:] = sorted(
            name for name in subdirectories if is_entered(Path(directory) / name, excluded)
        )
        found.extend(Path(directory, name).relative_to(root) for name in names if keep(name))
    return sorted(found)


def is_entered(directory: Path, excluded: Path | None) -> bool:
    if directory.name.startswith(".") or directory.name in NEVER_ENTERED:
        return False
    return excluded is None or directory.resolve() != excluded
