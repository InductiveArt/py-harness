import runpy
import sys
import traceback
from pathlib import Path

# A check that raises exits with this code instead of the 1 that means it found something.
CRASHED = 70


def main(argv: list[str]) -> int:
    """Runs a doctor check as the script it is, telling its crash apart from its finding."""
    script = Path(argv[1])
    sys.argv = [str(script)]
    # A script's own folder leads the import path, as when it runs directly.
    sys.path[0] = str(script.parent)
    try:
        runpy.run_path(str(script), run_name="__main__")
    # Whatever a check raises, it never reached its verdict.
    except Exception:  # noqa: BLE001
        traceback.print_exc()
        return CRASHED
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
