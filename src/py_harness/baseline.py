import subprocess
import sys
from pathlib import Path

from py_harness.console import err
from py_harness.console import out
from py_harness.suppressions import git
from py_harness.tables import as_list
from py_harness.tables import as_table
from py_harness.verdict import decoded
from py_harness.wiring import wiring_problems

# Where basedpyright keeps the errors it no longer reports; wiring refuses any other place.
BASELINE = Path(".basedpyright") / "baseline.json"
# basedpyright exits 1 when it found errors, which it has just recorded; above that it broke.
WRITTEN = (0, 1)


def main(argv: list[str]) -> int:
    """Records every type error the code has today, so a run reports only new ones."""
    harness, root = Path(argv[1]), Path.cwd()
    problems = wiring_problems(root, harness)
    if problems:
        for problem in problems:
            err(f"wiring: {problem}")
        err("baseline: nothing recorded, since the errors found would not be the harness's")
        return 1
    written = subprocess.run(["basedpyright", "--writebaseline"], check=False)  # noqa: S607
    if written.returncode not in WRITTEN:
        return written.returncode
    out(f"baseline: {recorded_line(root) or 'nothing to record, the code has no type error'}")
    return 0


def recorded_line(root: Path) -> str:
    """How many type errors the baseline holds, and how many the change since the last commit
    added or fixed; empty when there is no baseline."""
    now = recorded(read_baseline(root))
    if now is None:
        return ""
    if git(root, "rev-parse", "--verify", "--quiet", "HEAD") is None:
        return f"{now} recorded"
    change = now - (recorded(git(root, "show", f"HEAD:{BASELINE.as_posix()}")) or 0)
    return f"{now} recorded" + (f" ({change:+d} since the last commit)" if change else "")


def read_baseline(root: Path) -> str | None:
    try:
        return (root / BASELINE).read_text(encoding="utf-8")
    except OSError:
        return None


def recorded(text: str | None) -> int | None:
    """The errors a baseline holds, or None when there is none to read."""
    if text is None:
        return None
    files = as_table(as_table(decoded(text)).get("files"))
    return sum(len(as_list(entries)) for entries in files.values())


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
