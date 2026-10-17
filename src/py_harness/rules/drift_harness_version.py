from importlib.metadata import version
from pathlib import Path

from py_harness.console import out
from py_harness.projects import read_manifest
from py_harness.tables import string
from py_harness.tables import subtable
from py_harness.verdict import FAILED
from py_harness.verdict import PASSED
from py_harness.verdict import Verdict
from py_harness.verdict import report

PACKAGE = "py-harness"
# A release is a tag, read here without the letter a tag carries and a version does not.
TAG = "v"
UNPINNED = "pinned to no release"


def main() -> int:
    running = version(PACKAGE)
    pinned = pinned_release(Path.cwd())
    if pinned is None:
        # Whoever is changing the harness itself runs it from a path or a branch, which names
        # no release to hold the version to.
        out(f"doctor: harness-version OK ({running}, {UNPINNED})")
        report(Verdict(PASSED, f"{running}, {UNPINNED}"))
        return 0
    if pinned.removeprefix(TAG) == running:
        out(f"doctor: harness-version OK ({pinned})")
        report(Verdict(PASSED, pinned))
        return 0
    finding = f"pins {pinned} and runs {running}"
    out("The harness release pinned is not the one running:")
    out(f"  pyproject.toml  {finding}")
    out()
    out("Rule: the release a repository pins is the release it runs, so every repository the")
    out("harness checks can be told which bar it was held to. Run `uv sync` to install what is")
    out("pinned; where it is already installed, the release was cut without its version.")
    report(Verdict(FAILED, finding))
    return 1


def pinned_release(root: Path) -> str | None:
    """The release this repository pins the harness to, or None where it pins no release."""
    source = subtable(read_manifest(root), "tool", "uv", "sources", PACKAGE)
    return string(source, "tag")


if __name__ == "__main__":
    raise SystemExit(main())
