import json
from datetime import UTC
from datetime import datetime
from pathlib import Path
from typing import TypeAlias
from typing import cast

from py_harness.console import out
from py_harness.suppressions import git

# One event of the trail; every value is a string, so each line stays readable on its own.
Entry: TypeAlias = dict[str, str]
TRAIL = "py-harness/audit.jsonl"
SHOWN = 30
COLUMNS = ("time", "hook", "decision", "trigger", "target")


def trail(root: Path) -> Path | None:
    """This clone's audit trail, inside git's own folder, so no commit ever carries it."""
    location = git(root, "rev-parse", "--git-path", TRAIL)
    return None if location is None else root / location.strip()


def record(root: Path, entry: Entry) -> None:
    path = trail(root)
    if path is None:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    stamped = {"time": datetime.now(UTC).isoformat(timespec="seconds"), **entry}
    with path.open("a", encoding="utf-8") as file:
        file.write(json.dumps(stamped) + "\n")


def entries(root: Path) -> list[Entry]:
    path = trail(root)
    if path is None or not path.is_file():
        return []
    lines = path.read_text(encoding="utf-8").splitlines()
    # Only `record` writes this file: one object of strings per line.
    return [cast("Entry", json.loads(line)) for line in lines if line]


def main() -> int:
    shown = entries(Path.cwd())[-SHOWN:]
    if not shown:
        out("audit: nothing recorded in this clone yet")
        return 0
    for entry in shown:
        out("  ".join(entry.get(column, "") for column in COLUMNS).rstrip())
        for detail in ("said", "detail"):
            if entry.get(detail):
                out(f"    {detail}: {entry[detail]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
