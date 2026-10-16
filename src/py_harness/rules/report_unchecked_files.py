from pathlib import Path

from py_harness.console import out
from py_harness.findings import counted
from py_harness.tree import repository_files
from py_harness.units import IGNORE_FILE
from py_harness.units import ignored_paths
from py_harness.verdict import PASSED
from py_harness.verdict import Verdict
from py_harness.verdict import report

# Extensions holding code no stage of this repository reads. Python's own are left out,
# since the format, lint, typecheck and test stages already read them.
CODE = frozenset(
    {
        ".bash",
        ".c",
        ".cc",
        ".cjs",
        ".cpp",
        ".cs",
        ".fish",
        ".go",
        ".h",
        ".hpp",
        ".java",
        ".js",
        ".jsx",
        ".kt",
        ".kts",
        ".lua",
        ".mjs",
        ".php",
        ".pl",
        ".ps1",
        ".rb",
        ".rs",
        ".sh",
        ".sql",
        ".swift",
        ".ts",
        ".tsx",
        ".zsh",
    }
)
# A file with no extension holds code when it names the interpreter that runs it.
SHEBANG = b"#!"


def main() -> int:
    root = Path.cwd()
    unchecked = unchecked_files(root)
    if not unchecked:
        out("doctor: unchecked-files none")
        return 0
    out(f"Code no stage covers ({len(unchecked)}):")
    for path in unchecked:
        out(f"  {path}")
    out("A change to any of these passes unseen. Add a stage that checks it, delete it, or")
    out(f"list it in {IGNORE_FILE.as_posix()} to accept that nothing does.")
    report(Verdict(PASSED, counted(len(unchecked), "file")))
    return 0


def unchecked_files(root: Path) -> list[str]:
    ignored = ignored_paths(root)
    found = repository_files(root, lambda name: Path(name).suffix in CODE or not Path(name).suffix)
    return [
        path.as_posix()
        for path in found
        if holds_code(root / path) and not is_ignored(path, ignored)
    ]


def holds_code(path: Path) -> bool:
    return path.suffix in CODE or declares_an_interpreter(path)


def declares_an_interpreter(path: Path) -> bool:
    try:
        with path.open("rb") as file:
            return file.read(len(SHEBANG)) == SHEBANG
    except OSError:
        return False


def is_ignored(path: Path, ignored: set[str]) -> bool:
    """True for a file the ignore file names, or one below a directory it names."""
    return any(path.is_relative_to(Path(entry)) for entry in ignored)


if __name__ == "__main__":
    raise SystemExit(main())
