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
QUESTIONS = {
    "new file": "what will use it, and why no existing module fits",
    "destructive": "what it deletes or discards, and why",
    "wiring": "what changes in how the harness runs here",
}
# What the agent states before the change, for its own context: none of it is checked.
FACTS = {
    "new file": "who will use it, and which existing module could hold it instead: search for one",
    "wiring": "what changes in how the harness runs here, and the user's words asking for it",
}
HINTS = {
    "undo": "the command that reverses it",
    "asked": "the user's own words asking for it, copied exactly",
}
UNQUOTED = "its description quotes nothing the user said"
NO_TRANSCRIPT = "no transcript shows what the user said"
BASELINES = "baselines"


@dataclass(frozen=True)
class Change:
    trigger: str
    # The file for a file, the command for a command: what the block's CHANGE line names.
    target: str
    # The change as the user reads it: creating, editing or deleting a file, or running a command.
    doing: str

    @property
    def needs_the_users_words(self) -> bool:
        """Where new code lives is the agent's call; deleting or rewiring is the user's to ask."""
        return self.trigger != "new file"

    @property
    def fields(self) -> tuple[str, ...]:
        return ("why", "undo", "asked") if self.needs_the_users_words else ("why", "undo")

    def template(self) -> list[str]:
        """The block that tells the user about the change, its CHANGE line filled in."""
        hints = {**HINTS, "why": QUESTIONS[self.trigger]}
        return [
            f"CHANGE: {self.target}",
            *(f"{key.upper()}: <{hints[key]}>" for key in self.fields),
        ]


def main() -> int:
    # The client sends each hook event as a JSON object.
    payload = cast("dict[str, object]", json.loads(sys.stdin.read() or "{}"))
    root = Path(text(payload, "cwd") or Path.cwd())
    arguments = cast("dict[str, object]", payload.get("tool_input") or {})
    session = text(payload, "session_id") or "unknown"
    remember(root, session)
    described = " ".join(text(arguments, field) for field in DESCRIBING)
    pending = changes(text(payload, "tool_name"), arguments, root)
    held = [change for change in pending if not allowed(root, session, change, described, payload)]
    return KEEP_WORKING if held else 0


def changes(tool: str, arguments: dict[str, object], root: Path) -> list[Change]:
    if tool in SHELL:
        command = " ".join(text(arguments, "command").split())
        destructive = any(pattern.search(command) for pattern in DESTRUCTIVE)
        shown = command[:200]
        return [Change("destructive", shown, f"running `{shown}`")] if destructive else []
    if tool == PATCHING:
        patched = PATCHED_FILE.finditer(text(arguments, "input"))
        return [
            found
            for match in patched
            for found in file_change(root, match["action"], match["path"])
        ]
    path = text(arguments, "file_path") or text(arguments, "filePath") or text(arguments, "path")
    if tool not in EDITING or not path:
        return []
    return file_change(root, "Add" if tool in CREATING else "Update", path)


def file_change(root: Path, action: str, path: str) -> list[Change]:
    relative = Path(os.path.relpath(Path(root, path), root)).as_posix()
    overwriting = action == "Add" and (root / relative).exists()
    return judged("Update" if overwriting else action, relative)


def judged(action: str, relative: str) -> list[Change]:
    """The change an action on a file makes, of the kinds the gates cover."""
    if action == "Delete":
        return [Change("destructive", relative, f"deleting {relative}")]
    if action == "Add":
        return [Change("new file", relative, f"creating {relative}")]
    if Path(relative).name in WIRING:
        return [Change("wiring", relative, f"editing {relative}")]
    return []


def allowed(
    root: Path, session: str, change: Change, described: str, payload: dict[str, object]
) -> bool:
    """Lets a destructive change through on the user's own words, any other once it was held.

    The one hold of any other change asks the agent for the facts that bear on it, so they
    stand in its context as it makes the change. Outside git there is no trail, so nothing
    is held.
    """
    if trail(root) is None:
        return True
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


# #region The session's baseline


def remember(root: Path, session: str) -> None:
    """Keeps the working tree as the session's first gated call found it, before any change."""
    path = baseline(root, session)
    if path is None or path.exists():
        return
    found = tree(root)
    if found is None:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(found), encoding="utf-8")


def baseline(root: Path, session: str) -> Path | None:
    location = trail(root)
    name = re.sub(r"[^\w-]", "_", session)
    return None if location is None else location.parent / BASELINES / f"{name}.json"


def tree(root: Path) -> dict[str, str] | None:
    """Each path that differs from the last commit: how it differs, and its content's digest."""
    listed = git(root, "status", "--porcelain=v1", "-z", "--untracked-files=all", "--no-renames")
    if listed is None:
        return None
    # Each record is a two-letter status, a space, then the path.
    records = [status for status in listed.split("\0") if status]
    return {status[3:]: f"{action(status[:2])} {digest(root / status[3:])}" for status in records}


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
