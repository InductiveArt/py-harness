import os
import re
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

from py_harness.tables import Table
from py_harness.tables import as_list
from py_harness.tables import as_table
from py_harness.tables import integer
from py_harness.tables import items_of
from py_harness.tables import string
from py_harness.verdict import FAILED
from py_harness.verdict import Section
from py_harness.verdict import Verdict
from py_harness.verdict import decoded

FIRST = 5
# basedpyright announces each rewrite of its baseline on a line of its own, before its report.
BASELINE_UPDATED = re.compile(r"\Aupdated \S+ with \d+ errors? \([^)\n]*\)\n")


@dataclass(frozen=True)
class Finding:
    path: str
    line: int
    column: int
    rule: str
    message: str

    def text(self) -> str:
        """One line, the path first, so a search for the path finds it."""
        return f"{self.path}:{self.line}:{self.column}: {self.rule} {self.head()}"

    def lines(self) -> list[str]:
        """Under its file's heading: the place, then any further lines of the message."""
        return [f"{self.line}:{self.column}: {self.rule} {self.head()}", *self.rest()]

    def shown(self) -> list[str]:
        """On its own: the whole line, then any further lines of the message."""
        return [self.text(), *self.rest()]

    def head(self) -> str:
        return self.message.splitlines()[0] if self.message else ""

    def rest(self) -> list[str]:
        return [f"    {more.strip()}" for more in self.message.splitlines()[1:] if more.strip()]


@dataclass(frozen=True)
class Problem:
    """A test that failed, or a file of tests that could not run."""

    test: str
    message: str
    text: str


def counted(number: int, noun: str) -> str:
    return f"{number} {noun if number == 1 else noun + 's'}"


def relative(path: str, root: Path) -> str:
    """The path from the repository root, as every other line of the run shows it."""
    if not os.path.isabs(path):
        return path
    within = os.path.relpath(path, root)
    return path if within.startswith("..") else within


def by_file(findings: list[Finding], noun: str) -> Verdict:
    """Counted per rule, and listed file by file, the file with most findings first."""
    files: dict[str, list[Finding]] = {}
    for finding in findings:
        files.setdefault(finding.path, []).append(finding)
    order = sorted(files, key=lambda path: (-len(files[path]), path))
    rules = Counter(finding.rule for finding in findings)
    first = [finding.text() for path in order for finding in files[path]][:FIRST]
    sections = [
        Section(
            f"{path} ({len(files[path])})", [line for each in files[path] for line in each.lines()]
        )
        for path in order
    ]
    headline = f"{counted(len(findings), noun)} in {counted(len(files), 'file')}"
    return Verdict(FAILED, headline, dict(rules.most_common()), first, sections)


# #region Readers: each returns None when the output is not what the checker promises.


def ruff_findings(stdout: str, root: Path) -> list[Finding] | None:
    items = items_of(decoded(stdout))
    if items is None:
        return None
    return [ruff_finding(as_table(item), root) for item in items]


def ruff_finding(table: Table, root: Path) -> Finding:
    place = as_table(table.get("location"))
    return Finding(
        relative(string(table, "filename") or "", root),
        integer(place, "row") or 0,
        integer(place, "column") or 0,
        # Ruff gives a syntax error no rule code.
        string(table, "code") or "syntax",
        string(table, "message") or "",
    )


def basedpyright_errors(stdout: str, root: Path) -> list[Finding] | None:
    table = as_table(decoded(BASELINE_UPDATED.sub("", stdout, count=1)))
    if "generalDiagnostics" not in table:
        return None
    diagnostics = [as_table(item) for item in as_list(table.get("generalDiagnostics"))]
    return [
        basedpyright_finding(diagnostic, root)
        for diagnostic in diagnostics
        if string(diagnostic, "severity") == "error"
    ]


def basedpyright_finding(table: Table, root: Path) -> Finding:
    start = as_table(as_table(table.get("range")).get("start"))
    return Finding(
        relative(string(table, "file") or "", root),
        # Zero-based in the report, one-based wherever a person or an editor reads it.
        (integer(start, "line") or 0) + 1,
        (integer(start, "character") or 0) + 1,
        string(table, "rule") or "error",
        string(table, "message") or "",
    )


def unformatted(stdout: str, root: Path) -> list[str] | None:
    items = items_of(decoded(stdout))
    if items is None:
        return None
    files = {relative(string(as_table(item), "filename") or "", root) for item in items}
    return sorted(files)


def failed_tests(report: Path, root: Path) -> tuple[int, list[Problem]] | None:
    """How many tests passed, and every test or test file that did not."""
    try:
        table = as_table(decoded(report.read_text(encoding="utf-8")))
    except OSError:
        return None
    passed = integer(table, "passed")
    if passed is None:
        return None
    base = relative(string(table, "rootdir") or str(root), root)
    prefix = "" if base == "." else f"{base}/"
    problems = [as_table(item) for item in as_list(table.get("problems"))]
    return passed, [
        Problem(
            prefix + (string(problem, "test") or ""),
            string(problem, "message") or "",
            string(problem, "text") or "",
        )
        for problem in problems
    ]


def coverage_gaps(report: Path, root: Path) -> tuple[float, list[Section]] | None:
    """The share of code the tests run, and each file's lines and branches they never reach."""
    try:
        table = as_table(decoded(report.read_text(encoding="utf-8")))
    except OSError:
        return None
    percent = as_table(table.get("totals")).get("percent_covered")
    if not isinstance(percent, (int, float)) or isinstance(percent, bool):
        return None
    files = as_table(table.get("files"))
    gaps = [
        Section(relative(name, root), missing)
        for name in sorted(files)
        if (missing := missed(as_table(files[name])))
    ]
    return float(percent), gaps


def missed(file: Table) -> list[str]:
    lines = [item for item in as_list(file.get("missing_lines")) if isinstance(item, int)]
    branches = [branch(as_list(item)) for item in as_list(file.get("missing_branches"))]
    shown = [f"lines {spans(lines)}"] if lines else []
    return shown + ([f"branches {', '.join(branches)}"] if branches else [])


def branch(pair: list[object]) -> str:
    """A branch as source line, then target line; a negative target leaves the function."""
    ends = [str(end) if end >= 0 else "exit" for end in pair if isinstance(end, int)]
    return "->".join(ends)


def spans(numbers: list[int]) -> str:
    """Line numbers with each run of neighbours shortened to its ends: 6, 9-12."""
    runs: list[list[int]] = []
    for number in sorted(numbers):
        if runs and number == runs[-1][-1] + 1:
            runs[-1].append(number)
        else:
            runs.append([number])
    return ", ".join(str(run[0]) if len(run) == 1 else f"{run[0]}-{run[-1]}" for run in runs)
