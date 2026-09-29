import io
import re
import tokenize
from collections.abc import Iterator
from pathlib import Path

from py_harness.console import out
from py_harness.units import repository_python_files

# Each of these exempts more than its own line, or exempts without naming a
# rule, so no check can tell whether it still suppresses anything.
BLANKET = (
    (re.compile(r"#\s*pyright:(?!\s*ignore\b)"), "a type-checking directive for the whole file"),
    (re.compile(r"#\s*(?:ruff|flake8):\s*noqa"), "a lint exemption for the whole file"),
    (re.compile(r"#\s*fmt:\s*(?:off|skip)\b"), "a formatting exemption"),
    (re.compile(r"#\s*isort:\s*(?:skip_file|skip|off)\b"), "an import-sorting exemption"),
)
TYPE_IGNORE = re.compile(r"#\s*type:\s*ignore")


def main() -> int:
    python_files = repository_python_files(Path.cwd())
    violations = [violation for path in python_files for violation in exemptions(path)]
    if not violations:
        out("doctor: no-blanket-exemptions OK")
        return 0
    out("Blanket exemptions (forbidden):")
    for violation in violations:
        out(f"  {violation}")
    out()
    out("Rule: no file or region exempts itself from a check. Fix the finding, or suppress the")
    out("one line it is on with a comment that names the rule.")
    return 1


def exemptions(path: Path) -> Iterator[str]:
    source = path.read_text(encoding="utf-8")
    for token in tokenize.generate_tokens(io.StringIO(source).readline):
        kind = blanket_kind(token) if token.type == tokenize.COMMENT else None
        if kind is not None:
            yield f"{path.as_posix()}:{token.start[0]}  {kind}: `{token.string.strip()}`"


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
