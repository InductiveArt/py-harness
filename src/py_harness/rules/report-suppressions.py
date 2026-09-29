from collections import Counter
from pathlib import Path

from py_harness.console import out
from py_harness.suppressions import inventory
from py_harness.tree import repository_files

FILES_SHOWN = 10


def main() -> int:
    root = Path.cwd()
    present = inventory(root)
    if not present:
        out("doctor: suppressions none")
        return 0
    measured = density(len(present), python_lines(root))
    out(f"Suppressions in the repository ({len(present)}, {measured}):")
    for label, count in Counter(found.label for found in present).most_common():
        out(f"  {count:>4}  {label}")
    out("Files holding the most:")
    for path, count in Counter(found.path for found in present).most_common(FILES_SHOWN):
        out(f"  {count:>4}  {path}")
    return 0


def python_lines(root: Path) -> int:
    python_files = repository_files(root, lambda name: name.endswith(".py"))
    return sum(len((root / path).read_text(encoding="utf-8").splitlines()) for path in python_files)


def density(count: int, lines: int) -> str:
    if not lines:
        return "no lines of Python to compare with"
    return f"{1000 * count / lines:.1f} per 1,000 lines of Python"


if __name__ == "__main__":
    raise SystemExit(main())
