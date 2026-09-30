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
from py_harness.loop import digest
from py_harness.suppressions import git

# One line of the client's session transcript.
Event: TypeAlias = dict[str, object]

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
NEVER_ASKED_DONE = "If the user never asked for this, undo it and say so."
# One line of the block the agent writes to the user before a held change goes through.
FIELD = re.compile(r"^(?P<name>CHANGE|WHY|UNDO|ASKED): *(?P<value>.*)$")
# What an agent wraps a value in when it writes it as prose.
MARKS = "`\"'\u201c\u201d\u2018\u2019"
# Fewer words than this match some message by chance.
QUOTE_WORDS = 3
UNVERIFIED = "unverified: the client names no transcript to check"
NO_TRANSCRIPT = "no transcript shows what the user was told"
FIRST_HOLD = "is held until the user is told about it"
BASELINES = "baselines"


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
    remember(root, session)
    pending = changes(text(payload, "tool_name"), arguments, root)
    held = [change for change in pending if not allowed(root, session, change, payload)]
    return KEEP_WORKING if held else 0


# #region Before a change


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
    """The change an action on a file makes, of the kinds a hold covers."""
    if action == "Delete":
        return [Change("destructive", relative, f"deleting {relative}")]
    if action == "Add":
        return [Change("new file", relative, f"creating {relative}")]
    if Path(relative).name in WIRING:
        return [Change("wiring", relative, f"editing {relative}")]
    return []


def allowed(root: Path, session: str, change: Change, payload: dict[str, object]) -> bool:
    """Holds a change until the agent has written the user its block, then lets it through.

    Outside git there is no trail to remember a hold in, so nothing is held.
    """
    if trail(root) is None:
        return True
    entry = identity(session, change)
    last = latest(entries(root), entry)
    if last.get("decision") == "released" and change.lasts_the_session:
        return True
    events = transcript(payload)
    missing = answered(root, entry, change, events, last)
    if not missing:
        return True
    retried = last.get("decision") == "held"
    hold(root, entry, change, events, missing if retried else "")
    show(change, f"is still held: {missing}" if retried else FIRST_HOLD, done=False)
    return False


# #region At the end of a turn


def untold(root: Path, payload: dict[str, object], *, final: bool) -> list[Change]:
    """This session's changes in the working tree that no block told the user about, held.

    However a change was made, by a tool the gate saw or a command it could not read, the
    working tree shows it. The turn's final check holds nothing: what is still untold is
    released and marked so, since the agent may never manage to write its block.
    """
    session = text(payload, "session_id") or "unknown"
    events = transcript(payload)
    recorded = entries(root)
    pending: list[Change] = []
    for change in outcome(root, session):
        entry = identity(session, change)
        if told(recorded, entry):
            continue
        missing = answered(root, entry, change, events, latest(recorded, entry))
        if not missing:
            continue
        if final:
            record(root, {**entry, "decision": "released", "detail": f"untold: {missing}"})
            continue
        hold(root, entry, change, events, missing)
        show(change, f"was done, but {missing}", done=True)
        pending.append(change)
    return pending


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


def outcome(root: Path, session: str) -> list[Change]:
    """What this session changed in the working tree, of the kinds a hold covers."""
    path = baseline(root, session)
    now = tree(root)
    if path is None or not path.is_file() or now is None:
        return []
    # Only `remember` writes a baseline: one string per path.
    before = cast("dict[str, str]", json.loads(path.read_text(encoding="utf-8")))
    return [
        change
        for relative, state in now.items()
        if before.get(relative) != state
        for change in judged(state.split(" ")[0], relative)
    ]


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


def baseline(root: Path, session: str) -> Path | None:
    location = trail(root)
    name = re.sub(r"[^\w-]", "_", session)
    return None if location is None else location.parent / BASELINES / f"{name}.json"


def told(recorded: list[Entry], entry: Entry) -> bool:
    """A release this session covers the change: its own, or one for a command naming the file."""
    return any(
        past.get("decision") == "released"
        and all(past.get(field) == entry[field] for field in ("hook", "session", "trigger"))
        and entry["target"] in (past.get("target", ""), *past.get("target", "").split())
        for past in recorded
    )


# #region Holding and releasing


def identity(session: str, change: Change) -> Entry:
    return {
        "hook": "change-gate",
        "session": session,
        "trigger": change.trigger,
        "target": change.target,
    }


def latest(recorded: list[Entry], entry: Entry) -> Entry:
    earlier = [past for past in recorded if all(past.get(field) == entry[field] for field in entry)]
    return earlier[-1] if earlier else {}


def answered(
    root: Path, entry: Entry, change: Change, events: list[Event] | None, last: Entry
) -> str:
    """Releases the change once the agent has written its block; else says what is missing.

    A block counts from the change's last decision on, or from the start of the session
    when there is none.
    """
    if events is None and last.get("decision") == "held":
        record(root, {**entry, "decision": "released", "detail": UNVERIFIED})
        return ""
    if events is None:
        return NO_TRANSCRIPT
    said = said_since(events, int(last.get("mark") or 0))
    block = answer(change, said)
    missing = shortfall(change, said, block, typed(events))
    if not missing:
        answers = {field: block[field] for field in change.fields}
        record(root, {**entry, "decision": "released", "mark": str(len(events)), **answers})
    return missing


def hold(
    root: Path, entry: Entry, change: Change, events: list[Event] | None, missing: str
) -> None:
    mark = "" if events is None else str(len(events))
    record(root, {**entry, "decision": "held", "mark": mark, "detail": missing})


def show(change: Change, state: str, *, done: bool) -> None:
    """The block the agent must write the user, with its CHANGE line already filled in."""
    again = "end your turn again" if done else "make the same change again"
    err(f"py-harness change gate: {change.doing} {state}.")
    err("Write the user these lines as text in your reply, not in your reasoning:")
    err(f"neither the user nor this gate sees your reasoning. Then {again}.")
    err(f"CHANGE: {change.target}")
    hints = {**HINTS, "why": QUESTIONS[change.trigger]}
    for field in change.fields:
        err(f"{field.upper()}: <{hints[field]}>")
    if change.needs_the_users_words:
        err(NEVER_ASKED_DONE if done else NEVER_ASKED)


# #region Reading the transcript


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
        return "no text reached the user; reasoning does not count"
    if not block:
        return f"no message to the user has the line CHANGE: {change.target}"
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
    """What the user wrote or picked; text the client adds on its own, such as a skill, is meta."""
    return [
        " ".join(message.split())
        for event in events
        if event.get("type") == "user" and event.get("isMeta") is not True
        for message in (*texts(event), *picked(event))
    ]


def texts(event: Event) -> list[str]:
    """The text in an event's message; tool calls and their results are other blocks."""
    content = as_table(event.get("message")).get("content")
    if isinstance(content, str):
        return [content]
    if not isinstance(content, list):
        return []
    parts = [as_table(part) for part in cast("list[object]", content)]
    return [text(part, "text") for part in parts if part.get("type") == "text"]


def picked(event: Event) -> list[str]:
    """The user's answers to the agent's questions, which reach it as a tool's result."""
    given = as_table(as_table(event.get("toolUseResult")).get("answers"))
    return [value for value in given.values() if isinstance(value, str)]


def as_table(value: object) -> dict[str, object]:
    """A JSON object read as a table; any other value as an empty one."""
    return cast("dict[str, object]", value) if isinstance(value, dict) else {}


def text(table: dict[str, object], key: str) -> str:
    value = table.get(key)
    return value if isinstance(value, str) else ""


if __name__ == "__main__":
    raise SystemExit(main())
