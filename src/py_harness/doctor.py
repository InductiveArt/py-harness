import os
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from itertools import takewhile
from pathlib import Path

from py_harness.console import err
from py_harness.console import out
from py_harness.findings import counted
from py_harness.layout import source_packages
from py_harness.layout import source_root
from py_harness.units import UNITS_VARIABLE
from py_harness.units import UnitsError
from py_harness.units import encode_units
from py_harness.units import resolve_units
from py_harness.verdict import BROKEN
from py_harness.verdict import FAILED
from py_harness.verdict import PASSED
from py_harness.verdict import VERDICT_VARIABLE
from py_harness.verdict import Part
from py_harness.verdict import Section
from py_harness.verdict import Verdict
from py_harness.verdict import read_verdict
from py_harness.verdict import report

# The prefix is the kind: a rule fails on a violation, a drift fails on config
# that has diverged, a report only informs. Kinds run in this order.
KINDS = ("rule", "drift", "report")
SHARED_CHECKS = Path(__file__).parent / "rules"
LOCAL_CHECKS = Path(".py-harness") / "doctor"
HARNESS_VARIABLE = "PY_HARNESS_DIR"
# A check exits with this code when it found something; any other failing code is a crash.
FOUND = 1


@dataclass(frozen=True)
class Checked:
    name: str
    code: int
    lines: list[str]
    error: str
    # What the check reported, when it reported more than its output and its exit code.
    verdict: Verdict | None = None


def main(argv: list[str]) -> int:
    harness = argv[1]
    wanted = argv[2] if len(argv) > 2 else ""
    root = Path.cwd()
    try:
        units = resolve_units(root)
    except UnitsError as error:
        err(f"units: {error}")
        report(Verdict(FAILED, f"units: {error}"))
        return 1
    checks = [check for check in discover(root) if matches(check_name(check), wanted)]
    if not checks:
        out(f"doctor: no check matched '{wanted}'")
        report(Verdict(FAILED, f"no check matched '{wanted}'"))
        return 1
    for unit in units:
        if not source_packages(unit):
            source = source_root(unit).as_posix()
            err(f"doctor: '{unit.as_posix()}' has no package under {source}; source rules skip it")
    # Resolved once and handed down, so each exclusion is announced once
    # rather than once per check.
    environment = {**os.environ, UNITS_VARIABLE: encode_units(units), HARNESS_VARIABLE: harness}
    finished: list[Checked] = []
    previous: list[str] = []
    with tempfile.TemporaryDirectory(prefix="py-harness-doctor-") as scratch:
        for index, check in enumerate(checks):
            # Its own file per check, since every check inherits the doctor's own verdict path.
            handed = Path(scratch) / f"{index}.json"
            command = [sys.executable, "-m", "py_harness.guard", str(check)]
            completed = subprocess.run(
                command,
                env={**environment, VERDICT_VARIABLE: str(handed)},
                check=False,
                capture_output=True,
                text=True,
            )
            lines = completed.stdout.rstrip("\n").splitlines()
            show(lines, previous)
            sys.stderr.write(completed.stderr)
            previous = lines or previous
            finished.append(
                Checked(
                    check_name(check),
                    completed.returncode,
                    lines,
                    completed.stderr,
                    read_verdict(handed),
                )
            )
    verdict = judged(finished)
    report(verdict)
    return 0 if verdict.outcome == PASSED else 1


def judged(finished: list[Checked]) -> Verdict:
    """Fails on a check that found something; a check that crashed leaves the verdict unknown."""
    crashed = [check for check in finished if check.code not in (0, FOUND)]
    failed = [check for check in finished if check.code == FOUND]
    passed = len(finished) - len(crashed) - len(failed)
    headline = f"{passed} passed, {len(failed)} failed"
    headline += f", {len(crashed)} crashed" if crashed else ""
    parts = [part(check) for check in finished]
    if not crashed and not failed:
        return Verdict(PASSED, headline, parts=parts)
    named = [*crashed, *failed]
    first = [f"{check.name}: {gist(check)}" for check in named]
    outcome = BROKEN if crashed else FAILED
    return Verdict(outcome, headline, first=first, sections=telling(named), parts=parts)


def telling(named: list[Checked]) -> list[Section]:
    """Every finding in a file under that file, whatever check found it: the findings of
    one check together and in line order, each line naming the check that found it. A
    check that reported none keeps its whole output under its own name."""
    places: dict[str, list[str]] = {}
    plain: list[Section] = []
    for check in named:
        reported = check.verdict.sections if check.verdict is not None else []
        if check.code != FOUND or not reported:
            plain.append(Section(check.name, [*check.lines, *check.error.splitlines()]))
            continue
        for section in reported:
            found = places.setdefault(section.title, [])
            found.extend(f"{line}  ({check.name})" for line in section.lines)
    grouped = [Section(f"{title} ({len(lines)})", lines) for title, lines in places.items()]
    return [*grouped, *plain]


def part(check: Checked) -> Part:
    """The check's own line under the doctor's: how much it found, or what it said in passing."""
    if check.code == FOUND:
        if check.verdict is not None:
            return Part(check.name, FAILED, check.verdict.headline)
        found = findings(check.lines)
        return Part(check.name, FAILED, "" if found is None else counted(found, "finding"))
    if check.code != 0:
        return Part(check.name, BROKEN, f"crashed, exit {check.code}")
    if check.verdict is not None:
        return Part(check.name, PASSED, check.verdict.headline)
    return Part(check.name, PASSED, said(check))


def findings(lines: list[str]) -> int | None:
    """The lines listed under a check's heading at the least indent: one finding each."""
    headed = [index for index, line in enumerate(lines) if line.endswith(":") and line[:1] != " "]
    if not headed:
        return None
    listed = list(takewhile(lambda line: line[:1] == " " and line.strip(), lines[headed[0] + 1 :]))
    least = min((indent(line) for line in listed), default=None)
    return None if least is None else sum(indent(line) == least for line in listed)


def indent(line: str) -> int:
    return len(line) - len(line.lstrip())


def said(check: Checked) -> str:
    """A passing check's one-line verdict, when it says more than that the check passed."""
    if len(check.lines) != 1:
        return ""
    line = check.lines[0].removeprefix(f"doctor: {check.name.split('-', 1)[-1]} ")
    return "" if line == "OK" else line


def gist(check: Checked) -> str:
    """What says most in one line: a crash's last error, or a finding's heading and first item."""
    errors = [line for line in check.error.splitlines() if line.strip()]
    lines = [line.strip() for line in check.lines if line.strip()]
    if check.code != FOUND and errors:
        return errors[-1]
    if not lines:
        return "(no output)"
    # A check heads its findings with a line ending in a colon, then lists one per line.
    return " ".join(lines[:2]) if lines[0].endswith(":") and len(lines) > 1 else lines[0]


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
        (index, place, check)
        for place, directory in enumerate(directories)
        if directory.is_dir()
        for check in sorted(directory.glob("*.py"))
        if (index := kind_index(check)) is not None
    ]
    return [check for *_, check in sorted(found)]


def kind_index(check: Path) -> int | None:
    """Where a check's kind puts it in the run order; None when its name declares no kind."""
    for index, kind in enumerate(KINDS):
        if check.stem.startswith((f"{kind}-", f"{kind}_")):
            return index
    return None


def check_name(check: Path) -> str:
    """A check's name as a run shows it.

    A shared check is a module, so its name is spelled with underscores and shown with
    hyphens; a project's own check is a script, named with hyphens throughout.
    """
    if check.parent == SHARED_CHECKS:
        return check.stem.replace("_", "-")
    return check.stem


def matches(name: str, wanted: str) -> bool:
    if not wanted:
        return True
    return wanted in {name, name.split("-", 1)[1]}


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
