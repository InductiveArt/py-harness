import os
import subprocess
import sys
from pathlib import Path

from py_harness.console import err
from py_harness.console import out
from py_harness.layout import source_packages
from py_harness.layout import source_root
from py_harness.units import UNITS_VARIABLE
from py_harness.units import UnitsError
from py_harness.units import encode_units
from py_harness.units import resolve_units

# The prefix is the kind: a rule fails on a violation, a drift fails on config
# that has diverged, a report only informs. Kinds run in this order.
KINDS = ("rule-", "drift-", "report-")
SHARED_CHECKS = Path(__file__).parent / "rules"
LOCAL_CHECKS = Path(".py-harness") / "doctor"
HARNESS_VARIABLE = "PY_HARNESS_DIR"


def main(argv: list[str]) -> int:
    harness = argv[1]
    wanted = argv[2] if len(argv) > 2 else ""
    root = Path.cwd()
    try:
        units = resolve_units(root)
    except UnitsError as error:
        err(f"units: {error}")
        return 1
    checks = [check for check in discover(root) if matches(check, wanted)]
    if not checks:
        out(f"doctor: no check matched '{wanted}'")
        return 1
    for unit in units:
        if not source_packages(unit):
            source = source_root(unit).as_posix()
            err(f"doctor: '{unit.as_posix()}' has no package under {source}; source rules skip it")
    # Resolved once and handed down, so each exclusion is announced once
    # rather than once per check.
    environment = {**os.environ, UNITS_VARIABLE: encode_units(units), HARNESS_VARIABLE: harness}
    failed = False
    previous: list[str] = []
    for check in checks:
        command = [sys.executable, str(check)]
        completed = subprocess.run(
            command, env=environment, check=False, stdout=subprocess.PIPE, text=True
        )
        lines = completed.stdout.rstrip("\n").splitlines()
        show(lines, previous)
        previous = lines or previous
        failed = failed or completed.returncode != 0
    return 1 if failed else 0


def show(lines: list[str], previous: list[str]) -> None:
    """Lists one-line verdicts together, and sets longer output off from its neighbours."""
    if lines and previous and (len(lines) > 1 or len(previous) > 1):
        out()
    for line in lines:
        out(line)


def discover(root: Path) -> list[Path]:
    """Each kind in turn, the shared checks before the repo's own, which run without a fork."""
    directories = [SHARED_CHECKS, root / LOCAL_CHECKS]
    found = [
        (KINDS.index(kind), place, check)
        for place, directory in enumerate(directories)
        if directory.is_dir()
        for check in sorted(directory.glob("*.py"))
        for kind in KINDS
        if check.name.startswith(kind)
    ]
    return [check for *_, check in sorted(found)]


def matches(check: Path, wanted: str) -> bool:
    if not wanted:
        return True
    return wanted in {check.stem, check.stem.split("-", 1)[1]}


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
