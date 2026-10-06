from pathlib import Path
from typing import TypeAlias

from py_harness.logs import RUNNING
from py_harness.logs import RunRecord
from py_harness.logs import StageRecord
from py_harness.suppressions import Tally
from py_harness.verdict import BROKEN
from py_harness.verdict import FAILED
from py_harness.verdict import PASSED
from py_harness.verdict import SKIPPED

# Path to content digest for every file git reports as modified or untracked;
# None outside a git repository.
Snapshot: TypeAlias = dict[str, str] | None

INTERRUPTED = "interrupted"
WORDS = {PASSED: "ok", FAILED: "failed", BROKEN: "BROKEN", SKIPPED: "skipped"}
RULES_SHOWN = 6
TAIL = 10
# Lines a stage's log holds around its output: the stage's own heading, and make's notices.
FRAMING = ("→ ", "make: ", "make[")
CHANGED_SHOWN = 5
SUPPRESSION_LIMIT = 20
COMMON_SHOWN = 3


def outcome(stage: StageRecord) -> str:
    """A stage the record still holds as running was cut short with the run."""
    return INTERRUPTED if stage.outcome == RUNNING else stage.outcome


def stage_line(stage: StageRecord) -> str:
    state = outcome(stage)
    detail = f"{stage.headline} " if stage.headline else ""
    return f"  {stage.name:<11} {WORDS.get(state, state):<8} {detail}({stage.seconds:.1f}s)"


def stage_lines(stage: StageRecord) -> list[str]:
    """The stage's line, then a line for each check it is made of, indented under it."""
    width = max((len(part.name) for part in stage.parts), default=0)
    parts = [
        f"    {part.name:<{width}} {WORDS.get(part.outcome, part.outcome):<8} {part.headline}"
        for part in stage.parts
    ]
    return [stage_line(stage), *(line.rstrip() for line in parts)]


def render(record: RunRecord, root: Path, folder: str, *, table: bool) -> list[str]:
    """The stage table when asked, a block per stage that did not pass, then the verdict."""
    lines = [line for stage in record.stages for line in stage_lines(stage)] if table else []
    # Stages broken by one cause, such as a config every tool reads, share one telling of it.
    told: dict[tuple[str, ...], str] = {}
    for stage in record.stages:
        lines.extend(block(stage, root, told))
    lines.extend(record.footer)
    lines.extend(["", verdict_line(record, folder)])
    return lines


def block(stage: StageRecord, root: Path, told: dict[tuple[str, ...], str]) -> list[str]:
    state = outcome(stage)
    if state == BROKEN:
        return broken(stage, root, told)
    if state == INTERRUPTED:
        return [
            "",
            f"{stage.name}: the run stopped during this stage; its output so far: {stage.log}",
        ]
    if state != FAILED:
        return []
    lines = ["", f"{stage.name}: {stage.headline or 'failed'}"]
    if stage.counts:
        rules = list(stage.counts.items())
        listed = ", ".join(f"{rule} {number}" for rule, number in rules[:RULES_SHOWN])
        lines.append(f"  by rule: {listed}" + (", ..." if len(rules) > RULES_SHOWN else ""))
    if stage.first:
        lines.extend(f"  {line}" for line in stage.first)
        lines.append(f"  all of them: {stage.details or stage.log}")
    else:
        lines.extend([*tail(root / stage.log), f"  all of its output: {stage.log}"])
    return lines


def broken(stage: StageRecord, root: Path, told: dict[tuple[str, ...], str]) -> list[str]:
    """What the stage said last, or its own account of what broke, and where all of it is."""
    evidence = [f"  {line}" for line in stage.first] or tail(root / stage.log)
    cause = (stage.headline, *evidence)
    if cause in told:
        same = f"{stage.name} broke the same way as {told[cause]}"
        return ["", f"{same}; all of its output: {stage.log}"]
    told[cause] = stage.name
    return [
        "",
        f"{stage.name} broke: {stage.headline}.",
        "  Its result is unknown; this is not a finding in the code.",
        *evidence,
        f"  all of its output: {stage.log}",
        "  If those lines point at a file you changed, fix that file; otherwise tell the user.",
    ]


def tail(log: Path) -> list[str]:
    """The last lines a stage printed, without the lines that only frame its run."""
    try:
        text = log.read_text(encoding="utf-8")
    except OSError:
        return ["  (its output could not be read)"]
    lines = [line for line in text.splitlines() if line.strip() and not line.startswith(FRAMING)]
    if not lines:
        return ["  (it printed nothing)"]
    return ["  last lines of its output:", *(f"    {line}" for line in lines[-TAIL:])]


def verdict_line(record: RunRecord, folder: str) -> str:
    """The run's answer, on the last line, where a reader looks first."""
    seconds = sum(stage.seconds for stage in record.stages)
    states = [(stage.name, outcome(stage)) for stage in record.stages]
    if record.finished and all(state == PASSED for _, state in states):
        return f"{record.loop} passed in {seconds:.1f}s. Logs: {folder}"
    groups = [
        f"{state} {', '.join(name for name, found in states if found == state)}"
        for state in (BROKEN, FAILED, SKIPPED, INTERRUPTED)
        if any(found == state for _, found in states)
    ]
    return f"{record.loop} did not pass: {'; '.join(groups)}. Logs: {folder}"


def bare(states: list[tuple[str, str]], loop: str, folder: str) -> list[str]:
    """What the run can still say when its summary cannot be drawn: each stage, and the logs."""
    passed = all(state == PASSED for _, state in states)
    verdict = f"{loop} {'passed' if passed else 'did not pass'}. Logs: {folder}"
    return [*(f"  {name} {state}" for name, state in states), "", verdict]


def changed_line(changed: list[str] | None, listed: str) -> list[str]:
    """One line: the files themselves when they are few, else how many and where all are listed."""
    if changed is None:
        return ["", "Files changed during the run: unknown outside a git repository"]
    if not changed:
        return []
    named = ", ".join(changed) if len(changed) <= CHANGED_SHOWN else f"all of them: {listed}"
    return ["", f"Files changed during the run ({len(changed)}): {named}"]


def changed_files(before: dict[str, str], after: dict[str, str]) -> list[str]:
    """Compares content, so a file dirty before the run still shows when a fix rewrites it."""
    paths = before.keys() | after.keys()
    return sorted(path for path in paths if before.get(path) != after.get(path))


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
