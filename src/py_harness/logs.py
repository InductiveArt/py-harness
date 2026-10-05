import hashlib
import json
import tempfile
from dataclasses import asdict
from dataclasses import dataclass
from pathlib import Path

from py_harness.suppressions import git
from py_harness.tables import Table
from py_harness.tables import as_list
from py_harness.tables import as_table
from py_harness.tables import integer
from py_harness.tables import string
from py_harness.tables import string_list
from py_harness.verdict import decoded
from py_harness.verdict import write_whole

LOGS = "py-harness/logs"
RECORD = "run.json"
# A stage the run started and never saw end.
RUNNING = "running"


@dataclass(frozen=True)
class StageRecord:
    name: str
    outcome: str
    seconds: float
    headline: str
    counts: dict[str, int]
    first: list[str]
    # Each from the repository root: the stage's whole output, and its findings when it has any.
    log: str
    details: str | None


@dataclass(frozen=True)
class RunRecord:
    loop: str
    started: str
    finished: bool
    stages: list[StageRecord]
    # The run's closing lines that concern no single stage.
    footer: list[str]


def log_folder(root: Path, loop: str) -> Path:
    """The loop's folder inside git's own, so no commit carries it; outside git, a temporary one."""
    location = git(root, "rev-parse", "--git-path", f"{LOGS}/{loop}")
    if location is not None:
        return root / location.strip()
    tag = hashlib.sha1(str(root.resolve()).encode(), usedforsecurity=False).hexdigest()[:8]
    return Path(tempfile.gettempdir()) / f"py-harness-{root.resolve().name}-{tag}" / loop


def shown(path: Path, root: Path) -> str:
    """A path as a reader at the repository root types it."""
    try:
        return path.resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        return path.as_posix()


def write_record(folder: Path, record: RunRecord) -> None:
    write_whole(folder / RECORD, json.dumps(asdict(record)))


def read_record(folder: Path) -> RunRecord | None:
    try:
        table = as_table(decoded((folder / RECORD).read_text(encoding="utf-8")))
    except OSError:
        return None
    loop, started = string(table, "loop"), string(table, "started")
    if loop is None or started is None:
        return None
    stages = [stage_record(as_table(item)) for item in as_list(table.get("stages"))]
    finished = table.get("finished") is True
    return RunRecord(loop, started, finished, stages, string_list(table, "footer"))


def stage_record(table: Table) -> StageRecord:
    counts = as_table(table.get("counts"))
    seconds = table.get("seconds")
    return StageRecord(
        name=string(table, "name") or "?",
        outcome=string(table, "outcome") or RUNNING,
        seconds=float(seconds) if isinstance(seconds, (int, float)) else 0.0,
        headline=string(table, "headline") or "",
        counts={rule: number for rule in counts if (number := integer(counts, rule)) is not None},
        first=string_list(table, "first"),
        log=string(table, "log") or "",
        details=string(table, "details"),
    )
