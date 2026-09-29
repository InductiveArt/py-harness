import os
from pathlib import Path

from py_harness.console import out
from py_harness.doctor import HARNESS_VARIABLE

LINKS = Path(".claude") / "skills"
INSTRUCTIONS = (Path("CLAUDE.md"), Path(".claude") / "CLAUDE.md")
# Where an editor that reads instruction files by name, not by import, finds the rules.
RULES_LINK = Path(".github") / "instructions" / "py-harness.instructions.md"
RULES = "agent-rules.md"


def main() -> int:
    harness = Path(os.environ[HARNESS_VARIABLE])
    skills = sorted(path for path in (harness / "skills").iterdir() if path.is_dir())
    problems = [
        *import_problems(harness / RULES),
        *link_problems(RULES_LINK, harness / RULES),
        *(problem for skill in skills for problem in link_problems(LINKS / skill.name, skill)),
    ]
    if not problems:
        out("doctor: agent OK")
        return 0
    out("Agent layer not linked to the harness (drift):")
    for problem in problems:
        out(f"  {problem}")
    out()
    out("Rule: the agent reads the rules and skills of the harness version its checks come from:")
    out("CLAUDE.md imports the rules, and the rules and each skill are links, never copies.")
    return 1


def import_problems(rules: Path) -> list[str]:
    if any(imports(instructions, rules) for instructions in INSTRUCTIONS):
        return []
    return [f"CLAUDE.md does not import the agent rules; add the line: @{os.path.relpath(rules)}"]


def imports(instructions: Path, rules: Path) -> bool:
    """An import resolves from the directory of the file that holds it."""
    if not instructions.is_file():
        return False
    lines = (line.strip() for line in instructions.read_text(encoding="utf-8").splitlines())
    targets = (instructions.parent / line[1:] for line in lines if line.startswith("@"))
    return any(target.resolve() == rules.resolve() for target in targets)


def link_problems(link: Path, target: Path) -> list[str]:
    relative = os.path.relpath(target, link.parent)
    if not link.is_symlink():
        if link.exists():
            return [f"{link.as_posix()} is a copy; replace it with a link to {relative}"]
        create = f"mkdir -p {link.parent.as_posix()} && ln -s {relative} {link.as_posix()}"
        return [f"{link.as_posix()} is missing; create it with: {create}"]
    if link.resolve() != target.resolve():
        return [f"{link.as_posix()} links to {os.readlink(link)}, not to {relative}"]
    return []


if __name__ == "__main__":
    raise SystemExit(main())
