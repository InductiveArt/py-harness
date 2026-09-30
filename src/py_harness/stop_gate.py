import json
import subprocess
import sys
from pathlib import Path
from typing import cast

from py_harness.audit import record
from py_harness.change_gate import KEEP_WORKING
from py_harness.change_gate import untold
from py_harness.console import err
from py_harness.loop import snapshot

SUMMARY = "=== summary"
STATUS = "STATUS: "
UNTOLD = "changes no block told the user about"


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


if __name__ == "__main__":
    raise SystemExit(main())
