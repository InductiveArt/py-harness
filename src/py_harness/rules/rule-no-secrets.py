import subprocess
from pathlib import Path
from typing import TypeAlias

from detect_secrets.core.secrets_collection import SecretsCollection
from detect_secrets.settings import default_settings

from py_harness.console import out
from py_harness.suppressions import ALLOWLIST

# A comment ending its line with this allows a secret on that line, or with
# `nextline` on the line below, and must allow something. Anywhere else on a
# line the same words only mention the pragma.
ALLOWLIST_FILTER = "detect_secrets.filters.allowlist.is_line_allowlisted"
# Generated from package indexes; its hashes read as high-entropy strings.
GENERATED = frozenset({"uv.lock"})

Location: TypeAlias = tuple[str, int]


def main() -> int:
    files = committable_files(Path.cwd())
    if files is None:
        out("Secrets not scanned (forbidden):")
        out("  not a git repository, so which files would be committed is unknown")
        return 1
    findings = scan(files)
    pragmas = allowlist_pragmas(files)
    violations = [
        *(
            f"{file}:{line}  {', '.join(kinds)}"
            for (file, line), kinds in findings.items()
            if (file, line) not in pragmas.values()
        ),
        *(
            f"{file}:{line}  allowlist pragma allows nothing"
            for (file, line), allowed in pragmas.items()
            if allowed not in findings
        ),
    ]
    if not violations:
        out("doctor: no-secrets OK")
        return 0
    out("Secrets and unused allowlist pragmas (forbidden):")
    for violation in sorted(violations):
        out(f"  {violation}")
    out()
    out("Rule: no credential is committed; read it from the environment at runtime. A comment")
    out("ending in `pragma: allowlist secret` is for a value that only looks like one, and must")
    out("allow a finding.")
    return 1


def committable_files(root: Path) -> list[str] | None:
    listed = subprocess.run(
        ["git", "ls-files", "-z", "--cached", "--others", "--exclude-standard"],  # noqa: S607
        cwd=root,
        capture_output=True,
        check=False,
    )
    if listed.returncode != 0:
        return None
    paths = sorted({path for path in listed.stdout.decode().split("\0") if path})
    return [path for path in paths if Path(path).is_file() and Path(path).name not in GENERATED]


def scan(files: list[str]) -> dict[Location, list[str]]:
    """Every finding, pragmas ignored, so each pragma can be checked against what it allows."""
    collection = SecretsCollection()
    with default_settings() as settings:
        settings.disable_filters(ALLOWLIST_FILTER)
        for file in files:
            collection.scan_file(file)
    findings: dict[Location, list[str]] = {}
    for file, secret in collection:
        findings.setdefault((str(file), secret.line_number), []).append(secret.type)
    return {location: sorted(kinds) for location, kinds in findings.items()}


def allowlist_pragmas(files: list[str]) -> dict[Location, Location]:
    """Where each pragma is, mapped to the line it allows."""
    pragmas: dict[Location, Location] = {}
    for file in files:
        lines = Path(file).read_text(encoding="utf-8", errors="replace").splitlines()
        for number, line in enumerate(lines, start=1):
            found = ALLOWLIST.search(line)
            if found is not None:
                pragmas[(file, number)] = (file, number + 1 if found["nextline"] else number)
    return pragmas


if __name__ == "__main__":
    raise SystemExit(main())
