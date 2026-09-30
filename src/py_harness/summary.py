import re
from dataclasses import dataclass
from pathlib import Path
from typing import TypeAlias

from py_harness.suppressions import Tally

# Path to content digest for every file git reports as modified or untracked;
# None outside a git repository.
Snapshot: TypeAlias = dict[str, str] | None


@dataclass(frozen=True)
class Category:
    label: str
    pattern: re.Pattern[str]
    limit: int
    # A matching line heads a block: the indented lines under it belong to it.
    block: bool = False


CATEGORIES = (
    Category("Wiring problems", re.compile(r"^wiring: "), 20),
    Category("Agent layer", re.compile(r"^agent: "), 10),
    Category("Unformatted files", re.compile(r"^\S+:\d+:\d+: unformatted: "), 20),
    Category("Ruff diagnostics", re.compile(r"^\S+\.pyi?:\d+:\d+: [A-Z]+\d+ "), 20),
    Category("Type errors", re.compile(r" - error: "), 15),
    Category("Doctor findings", re.compile(r"\((?:forbidden|drift)\):$"), 30, block=True),
    Category("Test failures", re.compile(r"^(FAILED|ERROR)[ :]"), 20),
    Category("Coverage", re.compile(r"^FAIL Required test coverage"), 5),
    Category(
        "Uncovered lines (file, then the lines no test runs)",
        re.compile(r"^\S+\.py\s+\d+\s+\d+\s+\d+\s+\d+\s+\d+(?:\.\d+)?%\s+\S"),
        20,
    ),
)
FAILED_TARGET = re.compile(r"^make(?:\[\d+\])?: \*\*\* \[[^\]]*:\d+: (?P<target>[^\]]+)\] Error")
UNIT_MARKER = re.compile(r"^→ (?P<stage>\S+) (?P<unit>.+)$")
FALLBACK_LINES = 25
MODIFIED_LIMIT = 10
SUPPRESSION_LIMIT = 20
COMMON_SHOWN = 3


def summarize(
    log: str,
    status: int,
    before: Snapshot,
    after: Snapshot,
    suppressions: Tally,
    log_path: Path,
) -> list[str]:
    lines = log.splitlines()
    report = ["", f"=== summary (full log: {log_path}) ===", status_line(lines, status)]
    categorized = False
    for category in CATEGORIES:
        hits = hits_of(category, lines)
        if hits:
            categorized = True
            report.extend(excerpt(category.label, hits, category.limit))
    report.extend(modified_files(before, after))
    report.extend(suppression_lines(suppressions))
    if status != 0 and not categorized:
        tail = [line for line in lines if line.strip()][-FALLBACK_LINES:]
        report.extend(["", f"No categorized diagnostics; last {FALLBACK_LINES} non-empty lines:"])
        report.extend(f"  {line}" for line in tail)
    return report


def hits_of(category: Category, lines: list[str]) -> list[str]:
    hits: list[str] = []
    inside = False
    for line in lines:
        if category.pattern.search(line):
            hits.append(line)
            inside = category.block
        elif inside and line.startswith(" "):
            hits.append(line)
        else:
            inside = False
    return hits


def status_line(lines: list[str], status: int) -> str:
    if status == 0:
        return "STATUS: PASSED"
    failed = list(
        dict.fromkeys(found["target"] for line in lines if (found := FAILED_TARGET.match(line)))
    )
    if not failed:
        return "STATUS: FAILED (stage indeterminate; see the log tail below)"
    noun = "stage" if len(failed) == 1 else "stages"
    return f"STATUS: FAILED at {noun} {', '.join(described(target, lines) for target in failed)}"


def described(target: str, lines: list[str]) -> str:
    units = [
        found["unit"]
        for line in lines
        if (found := UNIT_MARKER.match(line)) and found["stage"] == target
    ]
    return f"`{target}` in `{units[-1]}`" if units else f"`{target}`"


def excerpt(label: str, hits: list[str], limit: int) -> list[str]:
    lines = ["", f"{label} ({len(hits)}):", *(f"  {hit}" for hit in hits[:limit])]
    if len(hits) > limit:
        lines.append(f"  ... ({len(hits)} total)")
    return lines


def modified_files(before: Snapshot, after: Snapshot) -> list[str]:
    """Compares content, so a file dirty before the run still shows when a fix rewrites it."""
    if before is None or after is None:
        return ["", "Files modified during run: unknown outside a git repository"]
    changed = sorted(
        path for path in before.keys() | after.keys() if before.get(path) != after.get(path)
    )
    if not changed:
        return []
    lines = ["", f"Files modified during run (auto-fix surface, {len(changed)}):"]
    lines.extend(f"  {path}" for path in changed[:MODIFIED_LIMIT])
    if len(changed) > MODIFIED_LIMIT:
        lines.append(f"  ... ({len(changed)} total)")
    return lines


def suppression_lines(tally: Tally) -> list[str]:
    """Shown on every run, passing ones included, since a suppression is how a check goes green."""
    lines = ["", headline(tally)]
    added = tally.added or []
    if added:
        lines.append(f"Added since the last commit ({len(added)}):")
        lines.extend(
            f"  {found.path}:{found.line}  {found.label}" for found in added[:SUPPRESSION_LIMIT]
        )
    if len(added) > SUPPRESSION_LIMIT:
        lines.append(f"  ... ({len(added)} total)")
    return lines


def headline(tally: Tally) -> str:
    count = len(tally.present)
    scale = f"{count} in the repository" if count else "none in the repository"
    if tally.change is None:
        direction = "; the change is unknown without a commit to compare"
    elif tally.change == 0:
        direction = ", unchanged since the last commit"
    else:
        direction = f", {tally.change:+d} since the last commit"
    common = ", ".join(f"{label} x{number}" for label, number in tally.most_common(COMMON_SHOWN))
    return f"Suppressions: {scale}{direction}" + (f" (most: {common})" if common else "")
