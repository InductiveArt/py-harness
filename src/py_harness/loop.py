import hashlib
import os
import signal
import subprocess
import sys
import time
from datetime import UTC
from datetime import datetime
from pathlib import Path
from types import FrameType

from py_harness.console import err
from py_harness.console import out
from py_harness.findings import counted
from py_harness.logs import RUNNING
from py_harness.logs import RunRecord
from py_harness.logs import StageRecord
from py_harness.logs import log_folder
from py_harness.logs import read_record
from py_harness.logs import shown
from py_harness.logs import write_record
from py_harness.summary import Snapshot
from py_harness.summary import bare
from py_harness.summary import changed_files
from py_harness.summary import changed_line
from py_harness.summary import render
from py_harness.summary import stage_lines
from py_harness.summary import suppression_lines
from py_harness.suppressions import tally
from py_harness.verdict import BROKEN
from py_harness.verdict import FAILED
from py_harness.verdict import PASSED
from py_harness.verdict import VERDICT_VARIABLE
from py_harness.verdict import Verdict
from py_harness.verdict import read_verdict

# Each loop answers one question. `check` and `ready` serve an agent at work,
# so they fix before they judge; `ci` judges code as committed, so it never
# rewrites a file and fails on what a fix would have repaired. Fixes run before
# any check, since removing an import changes the graph the doctor reads; lint
# fixes run before formatting, since a rewritten import may need wrapping again.
# Every stage runs even after one fails, so a single run names every failing stage.
FIXES = ("lint-fix", "format-fix")
CHECKS = ("format", "lint", "typecheck", "doctor")
LOOPS: dict[str, tuple[str, ...]] = {
    "check": ("agent", "wiring", *FIXES, *CHECKS, "test"),
    "ready": ("install", "agent", "wiring", *FIXES, *CHECKS, "coverage"),
    "ci": ("install", "wiring", *CHECKS, "coverage"),
}
# Stages that hand the run a verdict; the others are judged by their exit code alone.
REPORTING = frozenset({*FIXES, *CHECKS, "test", "coverage"})
# A CI server's console is the only log anyone keeps, so there each stage's output is shown.
STREAMED = frozenset({"ci"})
LIMIT_VARIABLE = "PY_HARNESS_STAGE_SECONDS"
LIMIT = 900
LAST = "last"
CHANGED = "changed.txt"
STOPPED = 130


class StopSignalError(Exception):
    """The run received a signal telling it to stop."""


def main(argv: list[str]) -> int:
    loop = argv[1] if len(argv) > 1 else ""
    root = Path.cwd()
    if loop == LAST:
        return last(root)
    stages = LOOPS.get(loop)
    if stages is None:
        err(f"usage: python -m py_harness.loop {'|'.join([*LOOPS, LAST])}")
        return 2
    folder = log_folder(root, loop)
    cleared(folder)
    for stop in (signal.SIGTERM, signal.SIGHUP):
        signal.signal(stop, interrupted)
    limit = int(os.environ.get(LIMIT_VARIABLE) or LIMIT)
    started = datetime.now(UTC).isoformat(timespec="seconds")
    before = snapshot(root)
    done: list[StageRecord] = []
    try:
        for name in stages:
            now = [*done, running(name, folder, root)]
            write_record(folder, RunRecord(loop, started, False, now, []))
            done.append(run_stage(name, folder, root, limit, stream=loop in STREAMED))
            for line in stage_lines(done[-1]):
                out(line)
    except (KeyboardInterrupt, StopSignalError):
        cut = [running(name, folder, root) for name in stages[len(done) : len(done) + 1]]
        record = RunRecord(loop, started, False, [*done, *cut], [])
        write_record(folder, record)
        closing(record, root, folder)
        return STOPPED
    footer = [*changes(before, snapshot(root), folder, root), *suppression_lines(tally(root))]
    record = RunRecord(loop, started, True, done, footer)
    write_record(folder, record)
    closing(record, root, folder)
    return 0 if all(stage.outcome == PASSED for stage in done) else 1


def interrupted(_signal: int, _frame: FrameType | None) -> None:
    raise StopSignalError


def cleared(folder: Path) -> None:
    """Empties the loop's folder, so nothing in it predates this run."""
    folder.mkdir(parents=True, exist_ok=True)
    for leftover in folder.iterdir():
        if leftover.is_file():
            leftover.unlink()


def running(name: str, folder: Path, root: Path) -> StageRecord:
    log = shown(folder / f"{name}.log", root)
    return StageRecord(name, RUNNING, 0.0, "", {}, [], log, None, [])


def run_stage(name: str, folder: Path, root: Path, limit: int, *, stream: bool) -> StageRecord:
    """Runs one stage in its own process group, its output written straight to its log."""
    log = folder / f"{name}.log"
    # Named for this run, so a stage left over from another can never answer for this one.
    handed = folder / f".{name}.{os.getpid()}.verdict"
    environment = {**os.environ, VERDICT_VARIABLE: str(handed)}
    before = snapshot(root) if name in FIXES else None
    started = time.monotonic()
    with log.open("w", encoding="utf-8") as sink:
        process = subprocess.Popen(
            ["make", "-s", name],  # noqa: S607
            stdout=sink,
            stderr=subprocess.STDOUT,
            env=environment,
            process_group=0,
        )
        code = waited(process, limit)
    seconds = time.monotonic() - started
    if stream:
        out(log.read_text(encoding="utf-8").rstrip("\n"))
    verdict = decided(name, code, read_verdict(handed), limit)
    handed.unlink(missing_ok=True)
    headline = verdict.headline
    if before is not None and verdict.outcome == PASSED:
        rewritten = changed_files(before, snapshot(root) or {})
        headline = f"rewrote {counted(len(rewritten), 'file')}" if rewritten else ""
    details = written(folder, name, verdict)
    return StageRecord(
        name,
        verdict.outcome,
        seconds,
        headline,
        verdict.counts,
        verdict.first,
        shown(log, root),
        None if details is None else shown(details, root),
        verdict.parts,
    )


def waited(process: subprocess.Popen[bytes], limit: int) -> int | None:
    """The stage's exit code, or None when it ran past its limit and was stopped."""
    try:
        return process.wait(timeout=limit)
    except subprocess.TimeoutExpired:
        ended(process)
        return None
    except (KeyboardInterrupt, StopSignalError):
        ended(process)
        raise


def ended(process: subprocess.Popen[bytes]) -> None:
    """Stops the stage and everything it started, which share its process group."""
    os.killpg(process.pid, signal.SIGKILL)
    process.wait()


def decided(name: str, code: int | None, verdict: Verdict | None, limit: int) -> Verdict:
    """The stage's own verdict, held to its exit code: a pass is never taken on its word alone."""
    if code is None:
        return Verdict(BROKEN, f"stopped after {limit}s, still running")
    if verdict is None:
        if name in REPORTING:
            return Verdict(BROKEN, f"stopped before it reported, exit {code}")
        return Verdict(PASSED) if code == 0 else Verdict(FAILED)
    if verdict.outcome == PASSED and code != 0:
        return Verdict(BROKEN, f"reported a pass but exited {code}")
    return verdict


def written(folder: Path, name: str, verdict: Verdict) -> Path | None:
    """Every finding, one section per file or test, so a reader opens only the part it needs."""
    if not verdict.sections:
        return None
    lines = [f"# {name}: {verdict.headline}"]
    if verdict.counts:
        rules = ", ".join(f"{rule} {number}" for rule, number in verdict.counts.items())
        lines.append(f"# by rule: {rules}")
    for section in verdict.sections:
        lines.extend(["", f"## {section.title}", *section.lines])
    path = folder / f"{name}.txt"
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def changes(before: Snapshot, after: Snapshot, folder: Path, root: Path) -> list[str]:
    """The line naming the files the run changed, with every one of them listed beside the logs."""
    if before is None or after is None:
        return changed_line(None, "")
    changed = changed_files(before, after)
    listed = folder / CHANGED
    if changed:
        listed.write_text("\n".join(changed) + "\n", encoding="utf-8")
    return changed_line(changed, shown(listed, root))


def closing(record: RunRecord, root: Path, folder: Path) -> None:
    for line in summary(record, root, shown(folder, root)):
        out(line)


def summary(record: RunRecord, root: Path, folder: str) -> list[str]:
    """The full summary, or, when it cannot be drawn, each stage's outcome and the logs."""
    try:
        return render(record, root, folder, table=False)
    # Whatever went wrong drawing it, the run still owes its verdict.
    except Exception:  # noqa: BLE001
        return bare([(stage.name, stage.outcome) for stage in record.stages], record.loop, folder)


def last(root: Path) -> int:
    """The newest run of any loop, drawn again from its record without running anything."""
    found = [
        (folder, record)
        for loop in LOOPS
        if (record := read_record(folder := log_folder(root, loop))) is not None
    ]
    if not found:
        out("No check, ready or ci run is recorded in this clone yet.")
        return 0
    folder, record = max(found, key=lambda pair: pair[1].started)
    for line in render(record, root, shown(folder, root), table=True):
        out(line)
    return 0


def snapshot(root: Path) -> Snapshot:
    listed = subprocess.run(
        ["git", "ls-files", "-z", "--modified", "--others", "--exclude-standard"],  # noqa: S607
        cwd=root,
        capture_output=True,
        check=False,
    )
    if listed.returncode != 0:
        return None
    paths = [path for path in listed.stdout.decode().split("\0") if path]
    return {path: digest(root / path) for path in paths}


def digest(path: Path) -> str:
    if not path.is_file():
        return "absent"
    return hashlib.sha1(path.read_bytes(), usedforsecurity=False).hexdigest()


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
