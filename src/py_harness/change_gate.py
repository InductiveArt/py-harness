import json
import os
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import cast

from py_harness.audit import Entry
from py_harness.audit import entries
from py_harness.audit import record
from py_harness.audit import trail
from py_harness.console import err
from py_harness.loop import digest
from py_harness.suppressions import git
from py_harness.transcript import UNVERIFIED
from py_harness.transcript import Event
from py_harness.transcript import quoted
from py_harness.transcript import text
from py_harness.transcript import transcript

# The agent host reads this exit code as "keep working", with standard error
# handed to the agent as the reason.
KEEP_WORKING = 2
# Tool names as the terminal agent and the editor's chat agent send them.
CREATING = frozenset({"Write", "create_file"})
EDITING = frozenset(
    {"Edit", "MultiEdit", "Write", "create_file", "replace_string_in_file", "insert_edit_into_file"}
)
SHELL = frozenset({"Bash", "run_in_terminal"})
PATCHING = "apply_patch"
# Where a tool call carries the agent's own words about it, as each agent names the field.
DESCRIBING = ("description", "explanation")
WIRING = frozenset({"pyproject.toml", "Makefile"})
DESTRUCTIVE = (
    re.compile(r"(?:^|[\s;&|(])rm\s"),
    re.compile(r"\bgit\s+reset\b[^;&|]*--hard"),
    re.compile(r"\bgit\s+clean\b"),
    re.compile(r"\bgit\s+push\b[^;&|]*(?:--force|\s-f\b)"),
    re.compile(r"\bgit\s+checkout\s+--"),
    re.compile(r"\bgit\s+restore\b(?![^;&|]*--staged)"),
    re.compile(r"\bgit\s+stash\s+(?:drop|clear)\b"),
    re.compile(r"\bgit\s+branch\s+-D\b"),
)
PATCHED_FILE = re.compile(
    r"^\*\*\* (?P<action>Add|Update|Delete) File: (?P<path>.+)$", re.MULTILINE
)
# What the agent states before the change, for its own context: none of it is checked.
FACTS = {
    "new file": "who will use it, and which existing module could hold it instead: search for one",
    "wiring": "what changes in how the harness runs here, and the user's words asking for it",
}
UNQUOTED = "its description quotes nothing the user said"
NO_TRANSCRIPT = "no transcript shows what the user said"
UNOWNED = "no repository holds this directory, and repositories lie below it"
STARTING_TREES = "starting-trees"
# How far below a directory a repository is looked for: clones are kept side by side, or
# one grouping folder deep.
REPOSITORY_DEPTH = 2


@dataclass(frozen=True)
class Change:
    trigger: str
    # The file for a file, the command for a command.
    target: str
    # The change as the user reads it: creating, editing or deleting a file, or running a command.
    doing: str
    # The repository the change is recorded in and judged against; None when none holds it.
    root: Path | None


def main() -> int:
    # The client sends each hook event as a JSON object.
    payload = cast("dict[str, object]", json.loads(sys.stdin.read() or "{}"))
    cwd = Path(text(payload, "cwd") or Path.cwd())
    arguments = cast("dict[str, object]", payload.get("tool_input") or {})
    session = text(payload, "session_id") or "unknown"
    remember(cwd, session)
    described = " ".join(text(arguments, field) for field in DESCRIBING)
    pending = changes(text(payload, "tool_name"), arguments, cwd)
    held = [change for change in pending if not allowed(cwd, session, change, described, payload)]
    return KEEP_WORKING if held else 0


def changes(tool: str, arguments: dict[str, object], cwd: Path) -> list[Change]:
    if tool in SHELL:
        command = " ".join(text(arguments, "command").split())
        if not any(pattern.search(command) for pattern in DESTRUCTIVE):
            return []
        shown = command[:200]
        return [Change("destructive", shown, f"running `{shown}`", repository(cwd))]
    if tool == PATCHING:
        patched = PATCHED_FILE.finditer(text(arguments, "input"))
        return [
            found for match in patched for found in file_change(cwd, match["action"], match["path"])
        ]
    path = text(arguments, "file_path") or text(arguments, "filePath") or text(arguments, "path")
    if tool not in EDITING or not path:
        return []
    return file_change(cwd, "Add" if tool in CREATING else "Update", path)


def file_change(cwd: Path, action: str, path: str) -> list[Change]:
    """The change an action on a file makes, in the paths of the repository holding the file.

    The file names the repository, so a change is judged where it lands rather than where the
    session happens to run. A file no repository holds leaves nothing to record it against.
    """
    located = Path(cwd, path)
    root = repository(located.parent)
    if root is None:
        return []
    relative = Path(os.path.relpath(located, root)).as_posix()
    overwriting = action == "Add" and located.exists()
    return judged(root, "Update" if overwriting else action, relative)


def judged(root: Path, action: str, relative: str) -> list[Change]:
    """The change an action on a file makes, of the kinds the gates cover."""
    if action == "Delete":
        return [Change("destructive", relative, f"deleting {relative}", root)]
    if action == "Add":
        return [Change("new file", relative, f"creating {relative}", root)]
    if Path(relative).name in WIRING:
        return [Change("wiring", relative, f"editing {relative}", root)]
    return []


def repository(start: Path) -> Path | None:
    """The root of the repository holding this path, or None when none does.

    A file being created has no directory of its own yet, so the nearest one that exists
    answers for it; every path reaches one by walking up.
    """
    existing = start
    while not existing.is_dir():
        existing = existing.parent
    found = git(existing, "rev-parse", "--show-toplevel")
    return None if found is None else Path(found.strip())


def repositories_below(cwd: Path, depth: int = REPOSITORY_DEPTH) -> bool:
    """Whether a repository lies below this directory, at the depths clones are kept at."""
    try:
        folders = [path for path in cwd.iterdir() if path.is_dir() and path.name[:1] != "."]
    except OSError:
        return False
    if any((folder / ".git").exists() for folder in folders):
        return True
    return depth > 1 and any(repositories_below(folder, depth - 1) for folder in folders)


def allowed(
    cwd: Path, session: str, change: Change, described: str, payload: dict[str, object]
) -> bool:
    """Lets a destructive change through on the user's own words, any other once it was held.

    The one hold of any other change asks the agent for the facts that bear on it, so they
    stand in its context as it makes the change.
    """
    root = change.root
    if root is None:
        return unowned(cwd, change)
    entry = identity("change-gate", session, change)
    last = latest(entries(root), entry)
    if change.trigger == "destructive":
        return confirmed(root, entry, change, described, transcript(payload), last)
    if last.get("decision") == "held":
        record(root, {**entry, "decision": "released"})
    if last:
        return True
    record(root, {**entry, "decision": "held"})
    err(f"py-harness change gate: before {change.doing}, present these facts:")
    err(f"{FACTS[change.trigger]}. Then make the same change again.")
    return False


def unowned(cwd: Path, change: Change) -> bool:
    """A change no repository holds, which is a command, since a file names its own.

    Where repositories lie below, the command can reach into any of them with nothing to
    record it against, so it is held; where none does, there is nothing to hold it for.
    """
    if not repositories_below(cwd):
        return True
    err(f"py-harness change gate: {change.doing} is held: {UNOWNED}.")
    err("Run it from inside the repository it affects, where the gate can record it.")
    return False


def confirmed(
    root: Path,
    entry: Entry,
    change: Change,
    described: str,
    events: list[Event] | None,
    last: Entry,
) -> bool:
    """A destructive change goes through when its description quotes the user asking for it."""
    if events is None and last.get("decision") == "held":
        record(root, {**entry, "decision": "released", "detail": UNVERIFIED})
        return True
    asked = "" if events is None else quoted(described, events)
    if asked:
        record(root, {**entry, "decision": "released", "asked": asked})
        return True
    reason = NO_TRANSCRIPT if events is None else UNQUOTED
    record(root, {**entry, "decision": "held", "detail": reason})
    err(f"py-harness change gate: {change.doing} is held: {reason}.")
    err("If the user asked for it, put their own words in the command's description, in")
    err("double quotes, three words at least, and run it again. If they never asked, do not")
    err("run it: ask them, and quote their answer. If git cannot restore what it deletes,")
    err("keep a copy first and say where.")
    return False


# #region The tree the session started from


def remember(root: Path, session: str) -> None:
    """Keeps the working tree as the session's first gated call found it, before any change."""
    path = starting_tree(root, session)
    if path is None or path.exists():
        return
    found = tree(root)
    if found is None:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(found), encoding="utf-8")


def starting_tree(root: Path, session: str) -> Path | None:
    location = trail(root)
    name = re.sub(r"[^\w-]", "_", session)
    return None if location is None else location.parent / STARTING_TREES / f"{name}.json"


def tree(root: Path) -> dict[str, str] | None:
    """Each path below the root that differs from the last commit: how it differs, and its
    content's digest.

    The `.` holds the answer to what lies below this root, which is the whole repository
    only when the root is its own. The format names a path from the repository root
    however deep it runs, so what leads here is taken off the front of each.
    """
    listed = git(
        root, "status", "--porcelain=v1", "-z", "--untracked-files=all", "--no-renames", "."
    )
    if listed is None:
        return None
    leading = (git(root, "rev-parse", "--show-prefix") or "").strip()
    # Each record is a two-letter status, a space, then the path.
    records = [status for status in listed.split("\0") if status]
    found = [(status[:2], status[3:].removeprefix(leading)) for status in records]
    return {path: f"{action(code)} {digest(root / path)}" for code, path in found}


def action(code: str) -> str:
    """The file action a status code records, named as the editor's patches name them."""
    if code == "??" or code[0] == "A":
        return "Add"
    return "Delete" if "D" in code else "Update"


# #region The trail


def identity(hook: str, session: str, change: Change) -> Entry:
    return {"hook": hook, "session": session, "trigger": change.trigger, "target": change.target}


def latest(recorded: list[Entry], entry: Entry) -> Entry:
    earlier = [past for past in recorded if all(past.get(field) == entry[field] for field in entry)]
    return earlier[-1] if earlier else {}


if __name__ == "__main__":
    raise SystemExit(main())
