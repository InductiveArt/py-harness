import hashlib
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import IO
from typing import cast

from py_harness.console import err
from py_harness.console import out
from py_harness.summary import Snapshot
from py_harness.summary import summarize
from py_harness.suppressions import tally

# Each loop answers one question. `check` and `ready` serve an agent at work,
# so they fix before they judge; `ci` judges code as committed, so it never
# rewrites a file and fails on what a fix would have repaired. Fixes run before
# any check, since removing an import changes the graph the doctor reads; lint
# fixes run before formatting, since a rewritten import may need wrapping again.
# Every check runs even after one fails, so a single run names every failing stage.
FIXES = ("lint-fix", "format-fix")
CHECKS = ("format", "lint", "typecheck", "doctor")
LOOPS: dict[str, tuple[str, ...]] = {
    "check": ("agent", *FIXES, *CHECKS, "test"),
    "ready": ("install", "agent", *FIXES, *CHECKS, "coverage"),
    "ci": ("install", *CHECKS, "coverage"),
}


def main(argv: list[str]) -> int:
    loop = argv[1] if len(argv) > 1 else ""
    stages = LOOPS.get(loop)
    if stages is None:
        err(f"usage: python -m py_harness.loop {'|'.join(LOOPS)}")
        return 2
    root = Path.cwd()
    log_path = Path(tempfile.gettempdir()) / f"{root.resolve().name}-{loop}.log"
    before = snapshot(root)
    status = run_logged(["make", "-s", "--keep-going", *stages], log_path)
    after = snapshot(root)
    suppressions = tally(root)
    for line in summarize(log_path.read_text(), status, before, after, suppressions, log_path):
        out(line)
    return status


def run_logged(command: list[str], log_path: Path) -> int:
    """Streams the command's combined output to the terminal and to the log as it arrives."""
    process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    with log_path.open("w") as log, process:
        # A text-mode pipe always exists and yields str; its stub leaves both open.
        for line in cast("IO[str]", process.stdout):
            out(line.removesuffix("\n"))
            log.write(line)
        return process.wait()


def snapshot(root: Path) -> Snapshot:
    listed = subprocess.run(
        ["git", "ls-files", "-z", "--modified", "--others", "--exclude-standard"],  # noqa: S607
        cwd=root,
        capture_output=True,
        check=False,
    )
    if listed.returncode != 0:
        return None
    paths = [path for path in listed.stdout.decode().split("\0") if path]
    return {path: digest(root / path) for path in paths}


def digest(path: Path) -> str:
    if not path.is_file():
        return "absent"
    return hashlib.sha1(path.read_bytes(), usedforsecurity=False).hexdigest()


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
