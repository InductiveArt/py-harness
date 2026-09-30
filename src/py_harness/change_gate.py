import json
import os
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import TypeAlias
from typing import cast

from py_harness.audit import Entry
from py_harness.audit import entries
from py_harness.audit import record
from py_harness.audit import trail
from py_harness.console import err
from py_harness.stop_gate import KEEP_WORKING

# One line of the client's session transcript.
Event: TypeAlias = dict[str, object]

# Tool names as the terminal agent and the editor's chat agent send them.
CREATING = frozenset({"Write", "create_file"})
EDITING = frozenset(
    {"Edit", "MultiEdit", "Write", "create_file", "replace_string_in_file", "insert_edit_into_file"}
)
SHELL = frozenset({"Bash", "run_in_terminal"})
PATCHING = "apply_patch"
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
HINTS = {
    "undo": "the command that reverses it",
    "asked": "the user's own words asking for it, copied exactly",
}
NEVER_ASKED = "If the user never asked for this, do not make it: ask them, or leave it and say so."
# One line of the block the agent writes to the user before a held change goes through.
FIELD = re.compile(r"^(?P<name>CHANGE|WHY|UNDO|ASKED): *(?P<value>.*)$")
# What an agent wraps a value in when it writes it as prose.
MARKS = "`\"'\u201c\u201d\u2018\u2019"
# Fewer words than this match some message by chance.
QUOTE_WORDS = 3
UNVERIFIED = "unverified: the client names no transcript to check"


@dataclass(frozen=True)
class Change:
    trigger: str
    # The file for a file, the command for a command: what the block's CHANGE line names.
    target: str
    # The change as the user reads it: creating, editing or deleting a file, or running a command.
    doing: str

    @property
    def lasts_the_session(self) -> bool:
        """A file answered for once stays answered; each destructive command answers anew."""
        return self.trigger != "destructive"

    @property
    def needs_the_users_words(self) -> bool:
        """Where new code lives is the agent's call; deleting or rewiring is the user's to ask."""
        return self.trigger != "new file"

    @property
    def fields(self) -> tuple[str, ...]:
        return ("why", "undo", "asked") if self.needs_the_users_words else ("why", "undo")


def main() -> int:
    # The client sends each hook event as a JSON object.
    payload = cast("dict[str, object]", json.loads(sys.stdin.read() or "{}"))
    root = Path(text(payload, "cwd") or Path.cwd())
    arguments = cast("dict[str, object]", payload.get("tool_input") or {})
    session = text(payload, "session_id") or "unknown"
    pending = changes(text(payload, "tool_name"), arguments, root)
    held = [change for change in pending if not allowed(root, session, change, payload)]
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
            for found in file_change(match["action"], match["path"], root)
        ]
    path = text(arguments, "file_path") or text(arguments, "filePath") or text(arguments, "path")
    if tool not in EDITING or not path:
        return []
    return file_change("Add" if tool in CREATING else "Update", path, root)


def file_change(action: str, path: str, root: Path) -> list[Change]:
    relative = Path(os.path.relpath(Path(root, path), root)).as_posix()
    if action == "Delete":
        return [Change("destructive", relative, f"deleting {relative}")]
    if action == "Add" and not (root / relative).exists():
        return [Change("new file", relative, f"creating {relative}")]
    if Path(relative).name in WIRING:
        return [Change("wiring", relative, f"editing {relative}")]
    return []


def allowed(root: Path, session: str, change: Change, payload: dict[str, object]) -> bool:
    """Holds a change until the agent has written the user its block, then lets its retry through.

    Outside git there is no trail to remember a hold in, so nothing is held.
    """
    if trail(root) is None:
        return True
    entry = {
        "hook": "change-gate",
        "session": session,
        "trigger": change.trigger,
        "target": change.target,
    }
    earlier = [past for past in entries(root) if all(past.get(key) == entry[key] for key in entry)]
    last = earlier[-1] if earlier else {}
    decision = last.get("decision", "")
    if decision == "released" and change.lasts_the_session:
        return True
    events = transcript(payload)
    mark = "" if events is None else str(len(events))
    if decision != "held":
        record(root, {**entry, "decision": "held", "mark": mark})
        show(change, "is held until the user is told about it")
        return False
    if events is None:
        record(root, {**entry, "decision": "released", "detail": UNVERIFIED})
        return True
    said = said_since(events, int(last.get("mark") or 0))
    block = answer(change, said)
    missing = shortfall(change, said, block, typed(events))
    if not missing:
        answered = {key: block[key] for key in change.fields}
        record(root, {**entry, "decision": "released", **answered})
        return True
    record(root, {**entry, "decision": "held", "mark": mark, "detail": missing})
    show(change, f"is still held: {missing}")
    return False


def show(change: Change, state: str) -> None:
    """The block the agent must write the user, with its CHANGE line already filled in."""
    err(f"py-harness change gate: {change.doing} {state}.")
    err("Write the user these lines as text in your reply, not in your reasoning:")
    err("neither the user nor this gate sees your reasoning. Then make the same change again.")
    err(f"CHANGE: {change.target}")
    hints = {**HINTS, "why": QUESTIONS[change.trigger]}
    for field in change.fields:
        err(f"{field.upper()}: <{hints[field]}>")
    if change.needs_the_users_words:
        err(NEVER_ASKED)


def answer(change: Change, said: list[str]) -> Entry:
    """The last block the agent wrote for this change, or nothing."""
    written = [block for block in blocks(said) if block.get("change") == change.target]
    return written[-1] if written else {}


def blocks(said: list[str]) -> list[Entry]:
    """Each CHANGE line in the agent's messages, with the field lines right under it."""
    found: list[Entry] = []
    for message in said:
        current: Entry | None = None
        for line in message.splitlines():
            field = FIELD.match(line.strip())
            if field is not None and field["name"] == "CHANGE":
                current = {}
                found.append(current)
            if field is None or current is None:
                current = None
            else:
                current[field["name"].lower()] = " ".join(field["value"].split()).strip(MARKS)
    return found


def shortfall(change: Change, said: list[str], block: Entry, words: list[str]) -> str:
    """Why the block does not yet answer for the change; empty once it does."""
    if not said:
        return "no text since the hold; reasoning does not count"
    if not block:
        return f"no message to the user since the hold has the line CHANGE: {change.target}"
    empty = [field.upper() for field in change.fields if not block.get(field)]
    if empty:
        return f"the block leaves {' and '.join(empty)} empty"
    quote = block.get("asked", "")
    if change.needs_the_users_words and len(quote.split()) < QUOTE_WORDS:
        return f"ASKED quotes fewer than {QUOTE_WORDS} words"
    if change.needs_the_users_words and not any(quote in message for message in words):
        return "the ASKED words are in no message from the user"
    return ""


def transcript(payload: dict[str, object]) -> list[Event] | None:
    """The session so far, when the client names its transcript."""
    path = Path(text(payload, "transcript_path"))
    if not path.name or not path.is_file():
        return None
    lines = path.read_text(encoding="utf-8").splitlines()
    # The client writes each event of the session as one JSON object per line.
    return [cast("Event", json.loads(line)) for line in lines if line]


def said_since(events: list[Event], mark: int) -> list[str]:
    """What the agent wrote for the user after the mark; its hidden reasoning is another block."""
    return [
        message
        for event in events[mark:]
        if event.get("type") == "assistant"
        for message in texts(event)
    ]


def typed(events: list[Event]) -> list[str]:
    """What the user wrote; text the client adds on its own, such as a loaded skill, is meta."""
    return [
        " ".join(message.split())
        for event in events
        if event.get("type") == "user" and event.get("isMeta") is not True
        for message in texts(event)
    ]


def texts(event: Event) -> list[str]:
    """The text in an event's message; tool calls and their results are other blocks."""
    message = event.get("message")
    if not isinstance(message, dict):
        return []
    content = cast("dict[str, object]", message).get("content")
    if isinstance(content, str):
        return [content]
    if not isinstance(content, list):
        return []
    parts = cast("list[dict[str, object]]", content)
    return [text(part, "text") for part in parts if part.get("type") == "text"]


def text(table: dict[str, object], key: str) -> str:
    value = table.get(key)
    return value if isinstance(value, str) else ""


if __name__ == "__main__":
    raise SystemExit(main())
