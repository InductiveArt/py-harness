import ast
import builtins
import io
import keyword
import re
import tokenize
from collections.abc import Iterator
from pathlib import Path

from py_harness.console import out
from py_harness.units import repository_python_files

URL = re.compile(r"https?://\S+")
BACKTICKED = re.compile(r"`[^`]+`")
# Prose never spells a word this way, so each of these shapes is a name.
CODE_SHAPED = re.compile(
    r"\b(?:[A-Za-z_]\w*\.)+[A-Za-z_]\w*\b"
    r"|\b[a-z][a-z0-9]*_\w+\b"
    r"|\b[A-Z][A-Z0-9]*_[A-Z0-9_]+\b"
)
WORD = re.compile(r"[A-Za-z_]\w*")
# The literals of the data formats a comment quotes most often count as vocabulary.
DATA_LITERALS = frozenset({"true", "false", "null"})
ALWAYS_IN_REACH = frozenset(dir(builtins)) | frozenset(keyword.kwlist) | DATA_LITERALS


def main() -> int:
    violations = [
        violation
        for path in repository_python_files(Path.cwd())
        for violation in out_of_reach(path)
    ]
    if not violations:
        out("doctor: no-comment-overreach OK")
        return 0
    out("Comments naming what their file cannot see (forbidden):")
    for violation in violations:
        out(f"  {violation}")
    out()
    out("Rule: a comment names only what its own file defines, imports or quotes.")
    out("State this file's contract instead of describing code that lives elsewhere.")
    return 1


def out_of_reach(path: Path) -> Iterator[str]:
    source = path.read_text(encoding="utf-8")
    documented = docstrings(ast.parse(source))
    reach = file_reach(source, documented) | set(path.with_suffix("").parts)
    for line, text in comments_and_docstrings(source, documented):
        missing = sorted(name for name in named(text) if not within(name, reach))
        if missing:
            yield f"{path.as_posix()}:{line}  {', '.join(missing)}"


def docstrings(tree: ast.Module) -> dict[tuple[int, int], str]:
    """Each docstring by the position of its string, which is prose and never code."""
    found: dict[tuple[int, int], str] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Module | ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef):
            docstring = ast.get_docstring(node, clean=False)
            if docstring is not None:
                found[(node.body[0].lineno, node.body[0].col_offset)] = docstring
    return found


def file_reach(source: str, documented: dict[tuple[int, int], str]) -> set[str]:
    """Every word the file's code or its strings contain, docstrings aside."""
    tokens = tokenize.generate_tokens(io.StringIO(source).readline)
    code = (token for token in tokens if is_wording(token) and token.start not in documented)
    return {word for token in code for word in whole_matches(WORD, token.string)}


def is_wording(token: tokenize.TokenInfo) -> bool:
    """A name, a string, or an f-string's literal text, which some versions split into parts."""
    return tokenize.tok_name[token.type] in {"NAME", "STRING", "FSTRING_MIDDLE"}


def comments_and_docstrings(
    source: str, documented: dict[tuple[int, int], str]
) -> list[tuple[int, str]]:
    tokens = tokenize.generate_tokens(io.StringIO(source).readline)
    found = [(token.start[0], token.string) for token in tokens if token.type == tokenize.COMMENT]
    found.extend((line, text) for (line, _), text in documented.items())
    return found


def named(text: str) -> set[str]:
    """Names a comment mentions: code-shaped words, and every word it puts in backticks."""
    prose = URL.sub("", text)
    names = {name for name in whole_matches(CODE_SHAPED, prose) if not is_abbreviation(name)}
    spans = whole_matches(BACKTICKED, prose)
    names.update(word for span in spans for word in whole_matches(WORD, span))
    return names


def is_abbreviation(name: str) -> bool:
    return all(len(part) == 1 for part in name.split("."))


def within(name: str, reach: set[str]) -> bool:
    return all(part in reach or part in ALWAYS_IN_REACH for part in whole_matches(WORD, name))


def whole_matches(pattern: re.Pattern[str], text: str) -> list[str]:
    """Each whole match, as a typed string rather than an untyped group."""
    return [match.group(0) for match in pattern.finditer(text)]


if __name__ == "__main__":
    raise SystemExit(main())
