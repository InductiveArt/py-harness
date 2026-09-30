import json
import os
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import cast

from py_harness.audit import entries
from py_harness.audit import record
from py_harness.audit import trail
from py_harness.console import err
from py_harness.stop_gate import KEEP_WORKING

# Tool names as the terminal agent and the editor's chat agent send them.
CREATING = frozenset({"Write", "create_file"})
EDITING = frozenset(
    {"Edit", "MultiEdit", "Write", "create_file", "replace_string_in_file", "insert_edit_into_file"}
)
SHELL = frozenset({"Bash", "run_in_terminal"})
PATCHING = "apply_patch"
WIRING = frozenset({"pyproject.toml", "Makefile"})
DESTRUCTIVE = (
    ("rm", re.compile(r"(?:^|[\s;&|(])rm\s")),
    ("git reset --hard", re.compile(r"\bgit\s+reset\b[^;&|]*--hard")),
    ("git clean", re.compile(r"\bgit\s+clean\b")),
    ("git push --force", re.compile(r"\bgit\s+push\b[^;&|]*(?:--force|\s-f\b)")),
    ("git checkout --", re.compile(r"\bgit\s+checkout\s+--")),
    ("git restore", re.compile(r"\bgit\s+restore\b(?![^;&|]*--staged)")),
    ("git stash drop", re.compile(r"\bgit\s+stash\s+(?:drop|clear)\b")),
    ("git branch -D", re.compile(r"\bgit\s+branch\s+-D\b")),
)
PATCHED_FILE = re.compile(
    r"^\*\*\* (?P<action>Add|Update|Delete) File: (?P<path>.+)$", re.MULTILINE
)
QUESTIONS = {
    "new file": "what will use it, and why no existing module fits",
    "destructive": "what it deletes or discards, and the one-line undo",
    "wiring": "what changes in how the harness runs here, and the user's words asking for it",
}
SAID_LIMIT = 600


@dataclass(frozen=True)
class Change:
    trigger: str
    # What a hold is matched by: the file for a file, the kind of command for a command.
    key: str
    target: str
    # The change as the user reads it: creating, editing or deleting a file, or running a command.
    doing: str

    @property
    def lasts_the_session(self) -> bool:
        """A file answered for once stays answered; each destructive command answers anew."""
        return self.trigger != "destructive"


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
        kinds = [label for label, pattern in DESTRUCTIVE if pattern.search(command)]
        return [
            Change("destructive", kind, command[:200], f"running `{command[:200]}`")
            for kind in kinds[:1]
        ]
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
        return [Change("destructive", f"delete {relative}", relative, f"deleting {relative}")]
    if action == "Add" and not (root / relative).exists():
        return [Change("new file", relative, relative, f"creating {relative}")]
    if Path(relative).name in WIRING:
        return [Change("wiring", relative, relative, f"editing {relative}")]
    return []


def allowed(root: Path, session: str, change: Change, payload: dict[str, object]) -> bool:
    """Holds a change once, and lets its retry through with what the agent said before it.

    Outside git there is no trail to remember a hold in, so nothing is held.
    """
    if trail(root) is None:
        return True
    earlier = [
        entry["decision"]
        for entry in entries(root)
        if entry.get("hook") == "change-gate"
        and entry.get("session") == session
        and entry.get("trigger") == change.trigger
        and entry.get("key") == change.key
    ]
    last = earlier[-1] if earlier else ""
    entry = {
        "hook": "change-gate",
        "session": session,
        "trigger": change.trigger,
        "key": change.key,
    }
    if last == "released" and change.lasts_the_session:
        return True
    if last == "held":
        record(
            root, {**entry, "decision": "released", "target": change.target, "said": said(payload)}
        )
        return True
    record(root, {**entry, "decision": "held", "target": change.target})
    question = QUESTIONS[change.trigger]
    err(f"py-harness change gate: before {change.doing}, tell the user in two or three lines")
    err(f"{question}. Then make the same change again.")
    return False


def said(payload: dict[str, object]) -> str:
    """The agent's last words before this change, when the client names its transcript."""
    path = Path(text(payload, "transcript_path"))
    if not path.name or not path.is_file():
        return ""
    last = ""
    for line in path.read_text(encoding="utf-8").splitlines():
        # A transcript line is one JSON object; an assistant's holds its message's content blocks.
        event = cast("dict[str, object]", json.loads(line))
        message = event.get("message")
        if event.get("type") != "assistant" or not isinstance(message, dict):
            continue
        blocks = cast("dict[str, object]", message).get("content")
        if isinstance(blocks, list):
            for block in cast("list[dict[str, object]]", blocks):
                if block.get("type") == "text":
                    last = text(block, "text")
    return " ".join(last.split())[:SAID_LIMIT]


def text(table: dict[str, object], key: str) -> str:
    value = table.get(key)
    return value if isinstance(value, str) else ""


if __name__ == "__main__":
    raise SystemExit(main())
