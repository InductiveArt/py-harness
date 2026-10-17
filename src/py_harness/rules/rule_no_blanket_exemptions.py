import ast
import io
import re
import tokenize
from collections.abc import Iterator
from pathlib import Path

from py_harness.console import out
from py_harness.findings import at_files
from py_harness.findings import counted
from py_harness.projects import repository_python_files
from py_harness.verdict import FAILED
from py_harness.verdict import Verdict
from py_harness.verdict import report

# Each of these exempts more than its own line, or exempts without naming a
# rule, so no check can tell whether it still suppresses anything.
BLANKET = (
    (re.compile(r"#\s*pyright:(?!\s*ignore\b)"), "a type-checking directive for the whole file"),
    (re.compile(r"#\s*(?:ruff|flake8):\s*noqa"), "a lint exemption for the whole file"),
    (re.compile(r"#\s*fmt:\s*(?:off|skip)\b"), "a formatting exemption"),
    (re.compile(r"#\s*isort:\s*(?:skip_file|skip|off)\b"), "an import-sorting exemption"),
)
TYPE_IGNORE = re.compile(r"#\s*type:\s*ignore")
# Either set to False leaves an xfail green whatever its test does: one stops the test from
# running, the other stops it from failing once the test passes again.
UNPROVEN_XFAIL = ("strict", "run")


def main() -> int:
    python_files = repository_python_files(Path.cwd())
    violations = [violation for path in python_files for violation in findings(path)]
    if not violations:
        out("doctor: no-blanket-exemptions OK")
        return 0
    out("Blanket exemptions (forbidden):")
    for place, number, finding in violations:
        out(f"  {place}:{number}  {finding}")
    out()
    out("Rule: no file or region exempts itself from a check, and an xfail proves its test still")
    out("fails. Fix the finding, or suppress the one line it is on with a comment that names")
    out("the rule.")
    report(Verdict(FAILED, counted(len(violations), "finding"), sections=at_files(violations)))
    return 1


def findings(path: Path) -> Iterator[tuple[str, int, str]]:
    source = path.read_text(encoding="utf-8")
    yield from exemptions(path, source)
    yield from unproven_xfails(path, source)


def unproven_xfails(path: Path, source: str) -> Iterator[tuple[str, int, str]]:
    for node in ast.walk(ast.parse(source, filename=str(path))):
        if not isinstance(node, ast.Call) or not is_xfail(node.func):
            continue
        for keyword in node.keywords:
            if keyword.arg in UNPROVEN_XFAIL and is_false(keyword.value):
                finding = f"an xfail that proves nothing: `{keyword.arg}=False`"
                yield path.as_posix(), node.lineno, finding


def is_xfail(node: ast.expr) -> bool:
    return isinstance(node, ast.Attribute) and node.attr == "xfail"


def is_false(node: ast.expr) -> bool:
    return isinstance(node, ast.Constant) and node.value is False


def exemptions(path: Path, source: str) -> Iterator[tuple[str, int, str]]:
    for token in tokenize.generate_tokens(io.StringIO(source).readline):
        kind = blanket_kind(token) if token.type == tokenize.COMMENT else None
        if kind is not None:
            yield path.as_posix(), token.start[0], f"{kind}: `{token.string.strip()}`"


def blanket_kind(comment: tokenize.TokenInfo) -> str | None:
    for pattern, kind in BLANKET:
        if pattern.match(comment.string):
            return kind
    # On a line of its own, a type ignore applies to the whole file.
    if TYPE_IGNORE.match(comment.string) and comment.line.lstrip().startswith("#"):
        return "a type-checking exemption for the whole file"
    return None


if __name__ == "__main__":
    raise SystemExit(main())
