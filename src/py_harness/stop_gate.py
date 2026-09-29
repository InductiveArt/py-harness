import json
import subprocess
import sys
from pathlib import Path
from typing import cast

from py_harness.console import err
from py_harness.loop import snapshot

# The agent host reads this exit code as "keep working", with standard error
# handed to the agent as the reason.
KEEP_WORKING = 2
SUMMARY = "=== summary"


def main() -> int:
    if already_held(sys.stdin.read()):
        return 0
    changes = snapshot(Path.cwd())
    if changes is not None and not changes:
        return 0
    checked = subprocess.run(["make", "-s", "check"], capture_output=True, text=True, check=False)  # noqa: S607
    if checked.returncode == 0:
        return 0
    start = checked.stdout.find(SUMMARY)
    err(checked.stdout[start:] if start >= 0 else checked.stdout + checked.stderr)
    err(
        "make check fails. Fix what it names before finishing, or tell the user why it cannot pass."
    )
    return KEEP_WORKING


def already_held(event: str) -> bool:
    """Whether the gate already held this stop, so an agent unable to fix a finding is released."""
    # The host sends each hook event as a JSON object.
    payload = cast("dict[str, object]", json.loads(event))
    return payload.get("stop_hook_active") is True


if __name__ == "__main__":
    raise SystemExit(main())
