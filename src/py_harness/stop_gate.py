import json
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
from py_harness.console import out
from py_harness.logs import log_folder
from py_harness.logs import read_record
from py_harness.logs import shown
from py_harness.loop import snapshot
from py_harness.summary import verdict_line
from py_harness.transcript import text

DONE = {"new file": "created", "destructive": "deleted", "wiring": "edited"}


def main() -> int:
    # The host sends each hook event as a JSON object.
    payload = cast("dict[str, object]", json.loads(sys.stdin.read()))
    root = Path.cwd()
    session = text(payload, "session_id") or "unknown"
    entry: Entry = {"hook": "stop-gate", "session": session}
    final = payload.get("stop_hook_active") is True
    if not final and failing(root, entry):
        return KEEP_WORKING
    if final:
        record(root, {**entry, "decision": "released"})
    show(unshown(root, session))
    return 0


def failing(root: Path, entry: Entry) -> bool:
    """Runs make check when anything changed; a failure keeps the agent working."""
    changes = snapshot(root)
    if changes is not None and not changes:
        return False
    checked = subprocess.run(["make", "-s", "check"], capture_output=True, text=True, check=False)  # noqa: S607
    if checked.returncode == 0:
        record(root, {**entry, "decision": "passed"})
        return False
    folder = log_folder(root, "check")
    run = read_record(folder)
    detail = "no record of the run" if run is None else verdict_line(run, shown(folder, root))
    record(root, {**entry, "decision": "held", "detail": detail})
    err(checked.stdout + checked.stderr)
    err(
        "make check fails. Fix what it names before finishing, or tell the user why it cannot pass."
    )
    return True


# #region Showing the user what changed


def unshown(root: Path, session: str) -> list[Change]:
    """This session's changes the user has not seen as they now stand, marked seen."""
    recorded = entries(root)
    fresh: list[Change] = []
    for change, state in outcome(root, session):
        entry = {**identity("stop-gate", session, change), "decision": "shown", "state": state}
        if latest(recorded, entry):
            continue
        record(root, entry)
        fresh.append(change)
    return fresh


def show(changes: list[Change]) -> None:
    """Tells the user, through the client, what the turn created, deleted or rewired."""
    if not changes:
        return
    lines = "".join(f"\n  {DONE[change.trigger]} {change.target}" for change in changes)
    # The client shows a hook's `systemMessage` to the user and nothing to the agent.
    out(json.dumps({"systemMessage": f"py-harness, what this turn changed:{lines}"}))


def outcome(root: Path, session: str) -> list[tuple[Change, str]]:
    """What this session changed in the working tree, of the kinds the gates cover."""
    path = baseline(root, session)
    now = tree(root)
    if path is None or not path.is_file() or now is None:
        return []
    # Only the change gate writes a baseline: one string per path.
    before = cast("dict[str, str]", json.loads(path.read_text(encoding="utf-8")))
    return [
        (change, state)
        for relative, state in now.items()
        if before.get(relative) != state
        for change in judged(since(state.split(" ")[0], relative in before), relative)
    ]


def since(action: str, found: bool) -> str:
    """The action as the session took it: a file it found, even untracked, it did not create."""
    return "Update" if action == "Add" and found else action


if __name__ == "__main__":
    raise SystemExit(main())
