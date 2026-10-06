import os
import subprocess
import sys
from pathlib import Path

from py_harness.console import out
from py_harness.layout import source_root
from py_harness.tables import read_table
from py_harness.tables import subtable
from py_harness.units import units_in_scope

# Neither kept nor broken: the linter could not check the contracts at all.
UNCHECKED = 2


def main() -> int:
    root = Path.cwd()
    if subtable(read_table(root / "pyproject.toml"), "tool", "importlinter") is None:
        out("doctor: boundaries skipped; pyproject.toml declares no [tool.importlinter] contracts")
        return 0
    # Each unit's own source goes first on the path, so the contracts judge
    # this tree and never an installed copy.
    sources = [str(source_root(unit).resolve()) for unit in units_in_scope(root)]
    inherited = [entry for entry in os.environ.get("PYTHONPATH", "").split(os.pathsep) if entry]
    environment = {**os.environ, "PYTHONPATH": os.pathsep.join([*sources, *inherited])}
    # The linter installed beside this interpreter, pinned with it.
    linter = str(Path(sys.executable).parent / "lint-imports")
    command = [linter, "--no-cache", "--no-logo"]
    linted = subprocess.run(command, env=environment, capture_output=True, text=True, check=False)
    if linted.returncode == 0:
        out("doctor: boundaries OK")
        return 0
    found = crossings((linted.stdout + linted.stderr).splitlines())
    if not found:
        # The linter failed without naming a crossing: its contracts could not be checked.
        sys.stderr.write(linted.stdout + linted.stderr)
        return UNCHECKED
    out("Broken layer contracts (forbidden):")
    for line in found:
        out(f"  {line}")
    return 1


def crossings(report: list[str]) -> list[str]:
    """One line per import that crosses a boundary, or module left out of the layers.

    The linter lists each under the heading it breaks, as a line opening with `- `;
    the further links of an indirect import follow it, indented, and stay under it.
    """
    found: list[str] = []
    heading = ""
    chained = False
    for line in report:
        if line.startswith("- "):
            found.append(f"{line.removeprefix('- ')}: {heading}")
            chained = True
        elif chained and line.startswith("  "):
            found.append(f"  {line.strip()}")
        else:
            chained = False
            heading = line.removesuffix(":") if line.endswith(":") else heading
    return found


if __name__ == "__main__":
    raise SystemExit(main())
