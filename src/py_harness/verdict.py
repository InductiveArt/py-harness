import json
import os
from dataclasses import asdict
from dataclasses import dataclass
from dataclasses import field
from pathlib import Path
from typing import cast

from py_harness.tables import Table
from py_harness.tables import as_list
from py_harness.tables import as_table
from py_harness.tables import integer
from py_harness.tables import string
from py_harness.tables import string_list

# A composed run names, for each stage it starts, the file that stage hands its verdict to.
VERDICT_VARIABLE = "PY_HARNESS_VERDICT"
PASSED = "passed"
FAILED = "failed"
BROKEN = "broken"
SKIPPED = "skipped"
OUTCOMES = (PASSED, FAILED, BROKEN, SKIPPED)


@dataclass(frozen=True)
class Section:
    title: str
    lines: list[str]


@dataclass(frozen=True)
class Verdict:
    """A stage's outcome, and what it found: counted per rule, the first few, and all by section."""

    outcome: str
    headline: str = ""
    counts: dict[str, int] = field(default_factory=dict[str, int])
    first: list[str] = field(default_factory=list[str])
    sections: list[Section] = field(default_factory=list[Section])


def report(verdict: Verdict) -> None:
    """Hands the verdict to the run that started the stage; a stage run alone tells no one."""
    target = os.environ.get(VERDICT_VARIABLE)
    if target:
        write_whole(Path(target), json.dumps(asdict(verdict)))


def write_whole(path: Path, text: str) -> None:
    """Replaces the file in one step, so a reader never sees it half written."""
    path.parent.mkdir(parents=True, exist_ok=True)
    partial = path.with_name(f"{path.name}.{os.getpid()}.partial")
    partial.write_text(text, encoding="utf-8")
    partial.replace(path)


def read_verdict(path: Path) -> Verdict | None:
    """The verdict at this path, or None when there is none or it is not one."""
    try:
        table = as_table(decoded(path.read_text(encoding="utf-8")))
    except OSError:
        return None
    outcome = string(table, "outcome")
    if outcome not in OUTCOMES:
        return None
    counts = as_table(table.get("counts"))
    return Verdict(
        outcome=outcome,
        headline=string(table, "headline") or "",
        counts={rule: number for rule in counts if (number := integer(counts, rule)) is not None},
        first=string_list(table, "first"),
        sections=[section(as_table(each)) for each in as_list(table.get("sections"))],
    )


def section(table: Table) -> Section:
    return Section(string(table, "title") or "", string_list(table, "lines"))


def decoded(text: str) -> object:
    """The JSON value the text holds, or None when it holds none."""
    try:
        return cast("object", json.loads(text))
    except ValueError:
        return None
