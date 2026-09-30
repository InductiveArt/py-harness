import os
import sys
from pathlib import Path

from py_harness.console import err
from py_harness.console import out
from py_harness.suppressions import git

# Agent clients load every file in the rules folder into each session, and a skill from the
# skills folder when the task calls for it.
RULES_LINK = Path(".claude") / "rules" / "py-harness.md"
SKILLS = Path(".claude") / "skills"
EXCLUDE_HEADING = "# py-harness agent layer: links into the pinned harness, never committed"


def main(argv: list[str]) -> int:
    harness = Path(argv[1])
    wanted = {RULES_LINK: harness / "agent-rules.md"}
    for skill in sorted((harness / "skills").iterdir()):
        wanted[SKILLS / skill.name] = skill
    placed = [link for link, target in wanted.items() if linked(link, target)]
    exclude(Path.cwd(), placed)
    return 0 if len(placed) == len(wanted) else 1


def linked(link: Path, target: Path) -> bool:
    """Points the link at its target, and leaves anything that is not a link where it is."""
    relative = os.path.relpath(target, link.parent)
    if link.is_symlink():
        if os.readlink(link) == relative:
            return True
        link.unlink()
    elif link.exists():
        err(f"agent: {link.as_posix()} is not a link; move it away so the harness can link it")
        return False
    link.parent.mkdir(parents=True, exist_ok=True)
    link.symlink_to(relative)
    out(f"linked {link.as_posix()}")
    return True


def exclude(root: Path, links: list[Path]) -> None:
    """Lists the links in this clone's own ignore file, so no commit ever carries them."""
    location = git(root, "rev-parse", "--git-path", "info/exclude")
    if location is None:
        return
    path = root / location.strip()
    present = path.read_text(encoding="utf-8").splitlines() if path.is_file() else []
    missing = [entry for entry in (f"/{link.as_posix()}" for link in links) if entry not in present]
    if not missing:
        return
    heading = [] if EXCLUDE_HEADING in present else ["", EXCLUDE_HEADING]
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as file:
        file.write("\n".join([*heading, *missing]) + "\n")


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
