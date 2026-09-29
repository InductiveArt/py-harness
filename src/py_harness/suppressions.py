import ast
import io
import re
import subprocess
import tokenize
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import TypeAlias

from py_harness.tree import repository_files
from py_harness.units import IGNORE_FILE

NOQA = re.compile(r"#\s*noqa\b(?::\s*(?P<codes>[A-Za-z]+\d+(?:[\s,]+[A-Za-z]+\d+)*))?")
TYPE_CHECKER_IGNORE = re.compile(
    r"#\s*(?P<tool>pyright|type):\s*ignore\b(?:\[(?P<rules>[^\]]*)\])?"
)
# A pragma counts only where it ends its line; anywhere else it is a mention.
ALLOWLIST = re.compile(r"pragma: allowlist (?P<nextline>nextline )?secret\s*$")
# A deleted file is named only on the old side of the diff, a created one only on the new.
OLD_FILE = re.compile(r"^--- a/(?P<path>.+)$")
DIFFED_FILE = re.compile(r"^\+\+\+ (?:b/(?P<path>.+)|/dev/null)$")
HUNK = re.compile(r"^@@ -\S+ \+(?P<start>\d+)")
EVERY_RULE = "(every rule)"

# Changed line numbers per file since the last commit; None marks a file whose every line is new.
Changes: TypeAlias = dict[str, set[int] | None]


@dataclass(frozen=True)
class Suppression:
    path: str
    line: int
    label: str
    text: str


@dataclass(frozen=True)
class Tally:
    """The repository's bypasses, and how the working tree changed them since the last commit."""

    present: list[Suppression]
    change: int | None
    added: list[Suppression] | None

    def most_common(self, count: int) -> list[tuple[str, int]]:
        return Counter(suppression.label for suppression in self.present).most_common(count)


def tally(root: Path) -> Tally:
    present = inventory(root)
    changed = changed_lines(root)
    if changed is None:
        return Tally(present, None, None)
    change = sum(
        len(suppressions_in_file(root, path)) - len(suppressions_at_head(root, path))
        for path in changed
    )
    return Tally(present, change, [found for found in present if is_added(found, changed)])


def inventory(root: Path) -> list[Suppression]:
    return [found for path in scanned_files(root) for found in suppressions_in_file(root, path)]


def is_added(found: Suppression, changed: Changes) -> bool:
    if found.path not in changed:
        return False
    lines = changed[found.path]
    return lines is None or found.line in lines


def scanned_files(root: Path) -> list[str]:
    files = [path.as_posix() for path in repository_files(root, lambda _name: True)]
    return [*files, IGNORE_FILE.as_posix()] if (root / IGNORE_FILE).is_file() else files


def suppressions_in_file(root: Path, path: str) -> list[Suppression]:
    file = root / path
    if not file.is_file():
        return []
    try:
        text = file.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        return []
    return suppressions_in_text(path, text)


def suppressions_at_head(root: Path, path: str) -> list[Suppression]:
    shown = git(root, "show", f"HEAD:{path}")
    return [] if shown is None else suppressions_in_text(path, shown)


# #region Reading one file


def suppressions_in_text(path: str, text: str) -> list[Suppression]:
    if path == IGNORE_FILE.as_posix():
        return excluded_units(path, text)
    if not path.endswith(".py"):
        lines = enumerate(text.splitlines(), start=1)
        return [
            Suppression(path, number, "allowlist secret", line.strip())
            for number, line in lines
            if ALLOWLIST.search(line)
        ]
    return [
        *(found for number, comment in comments(text) for found in parsed(path, number, comment)),
        *bypasses_in_code(path, text),
    ]


def comments(text: str) -> list[tuple[int, str]]:
    """Each comment by line; a file that does not tokenize is read line by line instead."""
    try:
        tokens = list(tokenize.generate_tokens(io.StringIO(text).readline))
    except (tokenize.TokenError, SyntaxError):
        return [(number, line.strip()) for number, line in enumerate(text.splitlines(), start=1)]
    return [
        (token.start[0], token.string.strip()) for token in tokens if token.type == tokenize.COMMENT
    ]


def parsed(path: str, number: int, comment: str) -> list[Suppression]:
    found: list[Suppression] = []
    for match in NOQA.finditer(comment):
        codes = re.split(r"[\s,]+", match["codes"].strip()) if match["codes"] else [EVERY_RULE]
        found.extend(Suppression(path, number, f"noqa {code}", comment) for code in codes)
    for match in TYPE_CHECKER_IGNORE.finditer(comment):
        rules = (
            [rule.strip() for rule in match["rules"].split(",")] if match["rules"] else [EVERY_RULE]
        )
        found.extend(
            Suppression(path, number, f"{match['tool']} {rule}", comment) for rule in rules
        )
    if ALLOWLIST.search(comment):
        found.append(Suppression(path, number, "allowlist secret", comment))
    return found


def bypasses_in_code(path: str, text: str) -> list[Suppression]:
    """Bypasses written as code rather than comments: an xfail, and a cast the checker trusts."""
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return []
    found: list[Suppression] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute) and node.attr == "xfail" and is_pytest(node.value):
            found.append(Suppression(path, node.lineno, "xfail", "pytest xfail"))
        elif isinstance(node, ast.Call) and is_cast(node.func):
            found.append(Suppression(path, node.lineno, "cast", "typing cast"))
    return found


def is_cast(node: ast.expr) -> bool:
    if isinstance(node, ast.Name):
        return node.id == "cast"
    return (
        isinstance(node, ast.Attribute)
        and node.attr == "cast"
        and isinstance(node.value, ast.Name)
        and node.value.id in {"typing", "typing_extensions"}
    )


def is_pytest(node: ast.expr) -> bool:
    if isinstance(node, ast.Name):
        return node.id == "pytest"
    return isinstance(node, ast.Attribute) and node.attr == "mark" and is_pytest(node.value)


def excluded_units(path: str, text: str) -> list[Suppression]:
    lines = enumerate(text.splitlines(), start=1)
    entries = ((number, line.split("#", 1)[0].strip()) for number, line in lines)
    return [Suppression(path, number, "excluded unit", entry) for number, entry in entries if entry]


# #region Comparing with the last commit


def changed_lines(root: Path) -> Changes | None:
    diff = git(root, "diff", "HEAD", "--unified=0", "--no-color")
    untracked = git(root, "ls-files", "-z", "--others", "--exclude-standard")
    if diff is None or untracked is None:
        return None
    return {**lines_in_diff(diff), **{path: None for path in untracked.split("\0") if path}}


def lines_in_diff(diff: str) -> Changes:
    changed: Changes = {}
    current: set[int] | None = None
    number = 0
    for line in diff.splitlines():
        if (found := OLD_FILE.match(line)) is not None:
            changed.setdefault(found["path"], set())
        elif (found := DIFFED_FILE.match(line)) is not None:
            current = None if found["path"] is None else changed.setdefault(found["path"], set())
        elif (found := HUNK.match(line)) is not None:
            number = int(found["start"])
        elif line.startswith("+") and current is not None:
            current.add(number)
            number += 1
    return changed


def git(root: Path, *arguments: str) -> str | None:
    command = ["git", *arguments]
    completed = subprocess.run(command, cwd=root, capture_output=True, text=True, check=False)
    return completed.stdout if completed.returncode == 0 else None
