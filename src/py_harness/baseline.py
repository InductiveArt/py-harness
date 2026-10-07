import subprocess
import sys
from pathlib import Path

from py_harness.console import err
from py_harness.console import out
from py_harness.suppressions import read_baseline
from py_harness.suppressions import recorded
from py_harness.suppressions import recorded_errors
from py_harness.wiring import wiring_problems

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
    held = recorded(root)
    if held is None:
        out("baseline: nothing to record, the code has no type error")
        return 0
    change = f" ({held.change:+d} since the last commit)" if held.change else ""
    out(f"baseline: {len(held.present)} recorded{change}")
    return 0


def recorded_line(root: Path) -> str:
    """How many type errors the baseline holds; empty when there is none."""
    text = read_baseline(root)
    return "" if text is None else f"{len(recorded_errors(text))} recorded"


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
