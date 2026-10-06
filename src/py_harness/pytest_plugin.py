import json
import os
from pathlib import Path

import pytest

from py_harness.verdict import write_whole

INTEGRATION = "integration"
# Either set to False ends an xfail without proving its test still fails.
UNPROVEN_XFAIL = ("strict", "run")
# A stage names the file the session reports to; a session started any other way reports nothing.
REPORT_VARIABLE = "PY_HARNESS_TEST_REPORT"
RECORDER = "py-harness-recorder"


class Recorder:
    """Keeps how many tests passed and every test or test file that did not, for the stage."""

    def __init__(self, target: Path) -> None:
        self.target = target
        self.passed = 0
        self.problems: list[dict[str, str]] = []

    def add(self, test: str, message: str, text: str) -> None:
        self.problems.append({"test": test, "message": message, "text": text})

    def pytest_collectreport(self, report: pytest.CollectReport) -> None:
        if report.failed:
            self.add(report.nodeid, "could not be collected", told(report))

    def pytest_runtest_logreport(self, report: pytest.TestReport) -> None:
        if report.failed:
            self.add(report.nodeid, cause(report.longreprtext, report.when), told(report))
        elif report.passed and report.when == "call":
            self.passed += 1

    def pytest_sessionfinish(self, session: pytest.Session) -> None:
        recorded = {
            "rootdir": str(session.config.rootpath),
            "passed": self.passed,
            "problems": self.problems,
        }
        write_whole(self.target, json.dumps(recorded))


def told(report: pytest.CollectReport | pytest.TestReport) -> str:
    """The failure as pytest prints it: the traceback, then what the test printed and logged."""
    captured = [f"--- {title}\n{content.rstrip()}" for title, content in report.sections]
    return "\n".join([report.longreprtext, *captured])


def cause(text: str, when: str) -> str:
    """The error line of a failure, as pytest marks it, with the phase when it is not the test."""
    marked = [line[1:].strip() for line in text.splitlines() if line.startswith("E ")]
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    shown = marked[0] if marked else (lines[-1] if lines else "")
    return shown if when == "call" else f"in {when}: {shown}"


def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line(
        "markers",
        f"{INTEGRATION}: needs more than its own unit; runs in test-integration, not in test",
    )
    target = os.environ.get(REPORT_VARIABLE)
    if target:
        config.pluginmanager.register(Recorder(Path(target)), RECORDER)


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    unproven = [
        (item.nodeid, key)
        for item in items
        for marker in item.iter_markers("xfail")
        for key in UNPROVEN_XFAIL
        if marker.kwargs.get(key) is False
    ]
    if not unproven:
        return
    recorder = config.pluginmanager.get_plugin(RECORDER)
    if isinstance(recorder, Recorder):
        for test, key in unproven:
            recorder.add(test, f"an xfail must prove its test still fails; drop {key}=False", "")
    listed = ", ".join(f"{test} ({key}=False)" for test, key in unproven)
    raise pytest.UsageError(f"an xfail must prove its test still fails; set neither: {listed}")
