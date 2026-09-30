import json
import re
import subprocess
import sys
from pathlib import Path
from typing import cast

from py_harness.audit import Entry
from py_harness.audit import entries
from py_harness.audit import record
from py_harness.change_gate import KEEP_WORKING
from py_harness.change_gate import Change
from py_harness.change_gate import baseline
from py_harness.change_gate import identity
from py_harness.change_gate import judged
from py_harness.change_gate import latest
from py_harness.change_gate import tree
from py_harness.console import err
from py_harness.loop import snapshot
from py_harness.transcript import UNVERIFIED
from py_harness.transcript import Event
from py_harness.transcript import misquoted
from py_harness.transcript import said_since
from py_harness.transcript import text
from py_harness.transcript import transcript

SUMMARY = "=== summary"
STATUS = "STATUS: "
UNTOLD = "changes no block told the user about"
NO_TRANSCRIPT = "no transcript shows what the user was told"
# One line of the block that tells the user about a change.
FIELD = re.compile(r"^(?P<name>CHANGE|WHY|UNDO|ASKED): *(?P<value>.*)$")
# What an agent wraps a value in when it writes it as prose.
MARKS = "`\"'\u201c\u201d\u2018\u2019"
NEVER_ASKED = "If the user never asked for this, undo it and say so."


def main() -> int:
    # The host sends each hook event as a JSON object.
    payload = cast("dict[str, object]", json.loads(sys.stdin.read()))
    root = Path.cwd()
    entry = {"hook": "stop-gate", "session": str(payload.get("session_id") or "unknown")}
    final = payload.get("stop_hook_active") is True
    unannounced = untold(root, payload, final=final)
    if final:
        record(root, {**entry, "decision": "released"})
        return 0
    changes = snapshot(root)
    checked = check() if changes is None or changes else None
    if checked is not None and checked.returncode != 0:
        status = next((line for line in checked.stdout.splitlines() if line.startswith(STATUS)), "")
        record(root, {**entry, "decision": "held", "detail": status})
        start = checked.stdout.find(SUMMARY)
        err(checked.stdout[start:] if start >= 0 else checked.stdout + checked.stderr)
        err(
            "make check fails. Fix what it names before finishing,"
            " or tell the user why it cannot pass."
        )
        return KEEP_WORKING
    if unannounced:
        record(root, {**entry, "decision": "held", "detail": UNTOLD})
        return KEEP_WORKING
    if checked is not None:
        record(root, {**entry, "decision": "passed"})
    return 0


def check() -> subprocess.CompletedProcess[str]:
    return subprocess.run(["make", "-s", "check"], capture_output=True, text=True, check=False)  # noqa: S607


# #region Telling the user


def untold(root: Path, payload: dict[str, object], *, final: bool) -> list[Change]:
    """This session's changes in the working tree that no block told the user about, held.

    However a change was made, by a tool the change gate saw or a command it could not
    read, the working tree shows it. The turn's final check holds nothing: what is still
    untold is released and marked so, since the agent may never manage to write its block.
    """
    session = text(payload, "session_id") or "unknown"
    events = transcript(payload)
    # The client writes the closing message to the transcript only after this hook reads it.
    closing = text(payload, "last_assistant_message")
    recorded = entries(root)
    pending: list[Change] = []
    for change in outcome(root, session):
        entry = identity("stop-gate", session, change)
        last = latest(recorded, entry)
        if last.get("decision") == "released":
            continue
        missing = answered(root, entry, change, events, last, closing)
        if not missing:
            continue
        if final:
            record(root, {**entry, "decision": "released", "detail": f"untold: {missing}"})
            continue
        mark = "" if events is None else str(len(events))
        record(root, {**entry, "decision": "held", "mark": mark, "detail": missing})
        show(change, missing)
        pending.append(change)
    return pending


def outcome(root: Path, session: str) -> list[Change]:
    """What this session changed in the working tree, of the kinds the gates cover."""
    path = baseline(root, session)
    now = tree(root)
    if path is None or not path.is_file() or now is None:
        return []
    # Only the change gate writes a baseline: one string per path.
    before = cast("dict[str, str]", json.loads(path.read_text(encoding="utf-8")))
    return [
        change
        for relative, state in now.items()
        if before.get(relative) != state
        for change in judged(since(state.split(" ")[0], relative in before), relative)
    ]


def since(action: str, found: bool) -> str:
    """The action as the session took it: a file it found, even untracked, it did not create."""
    return "Update" if action == "Add" and found else action


def answered(
    root: Path, entry: Entry, change: Change, events: list[Event] | None, last: Entry, closing: str
) -> str:
    """Releases the change once the agent's text carries its block; else says what is missing.

    A block counts from the change's last hold on, or from the start of the session.
    """
    if events is None and last.get("decision") == "held":
        record(root, {**entry, "decision": "released", "detail": UNVERIFIED})
        return ""
    if events is None:
        return NO_TRANSCRIPT
    said = [*said_since(events, int(last.get("mark") or 0)), *([closing] if closing else [])]
    block = answer(change, said)
    missing = shortfall(change, said, block, events)
    if not missing:
        told = {field: block[field] for field in change.fields}
        record(root, {**entry, "decision": "released", **told})
    return missing


def show(change: Change, missing: str) -> None:
    err(f"py-harness stop gate: {change.doing} was done, but {missing}.")
    err("End your turn with these lines, filled in, as text in your closing message,")
    err("not in your reasoning:")
    for line in change.template():
        err(line)
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
                current[field["name"].lower()] = unwrapped(" ".join(field["value"].split()))
    return found


def unwrapped(value: str) -> str:
    """The value without the quote marks or backticks around the whole of it, if any."""
    wrapped = len(value) > 1 and value[0] in MARKS and value[-1] in MARKS
    return value[1:-1] if wrapped else value


def shortfall(change: Change, said: list[str], block: Entry, events: list[Event]) -> str:
    """Why the block does not yet tell the user about the change; empty once it does."""
    if not said:
        return "no text reached the user; reasoning does not count"
    if not block:
        return f"no message to the user has the line CHANGE: {change.target}"
    empty = [field.upper() for field in change.fields if not block.get(field)]
    if empty:
        return f"the block leaves {' and '.join(empty)} empty"
    wrong = misquoted(block["asked"], events) if change.needs_the_users_words else ""
    return f"its ASKED line: {wrong}" if wrong else ""


if __name__ == "__main__":
    raise SystemExit(main())
