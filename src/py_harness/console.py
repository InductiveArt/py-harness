import sys


def out(line: str = "") -> None:
    """Writes and flushes, so a line lands before the output of any process started after it."""
    sys.stdout.write(f"{line}\n")
    sys.stdout.flush()


def err(line: str) -> None:
    sys.stderr.write(f"{line}\n")
