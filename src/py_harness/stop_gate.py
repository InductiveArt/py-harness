import json
import subprocess
import sys
from pathlib import Path
from typing import cast

from py_harness.audit import record
from py_harness.console import err
from py_harness.loop import snapshot

# The agent host reads this exit code as "keep working", with standard error
# handed to the agent as the reason.
KEEP_WORKING = 2
SUMMARY = "=== summary"
STATUS = "STATUS: "


def main() -> int:
    # The host sends each hook event as a JSON object.
    payload = cast("dict[str, object]", json.loads(sys.stdin.read()))
    root = Path.cwd()
    entry = {"hook": "stop-gate", "session": str(payload.get("session_id") or "unknown")}
    if payload.get("stop_hook_active") is True:
        record(root, {**entry, "decision": "released"})
        return 0
    changes = snapshot(root)
    if changes is not None and not changes:
        return 0
    checked = subprocess.run(["make", "-s", "check"], capture_output=True, text=True, check=False)  # noqa: S607
    if checked.returncode == 0:
        record(root, {**entry, "decision": "passed"})
        return 0
    status = next((line for line in checked.stdout.splitlines() if line.startswith(STATUS)), "")
    record(root, {**entry, "decision": "held", "detail": status})
    start = checked.stdout.find(SUMMARY)
    err(checked.stdout[start:] if start >= 0 else checked.stdout + checked.stderr)
    err(
        "make check fails. Fix what it names before finishing, or tell the user why it cannot pass."
    )
    return KEEP_WORKING


if __name__ == "__main__":
    raise SystemExit(main())
