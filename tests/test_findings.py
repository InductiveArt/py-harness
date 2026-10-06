import json
from pathlib import Path

import pytest

from py_harness.findings import Finding
from py_harness.findings import Problem
from py_harness.findings import basedpyright_errors
from py_harness.findings import by_file
from py_harness.findings import coverage_gaps
from py_harness.findings import failed_tests
from py_harness.findings import relative
from py_harness.findings import ruff_findings
from py_harness.findings import unformatted
from py_harness.logs import RunRecord
from py_harness.logs import StageRecord
from py_harness.logs import read_record
from py_harness.logs import write_record
from py_harness.verdict import FAILED
from py_harness.verdict import PASSED
from py_harness.verdict import VERDICT_VARIABLE
from py_harness.verdict import Part
from py_harness.verdict import Section
from py_harness.verdict import Verdict
from py_harness.verdict import read_verdict
from py_harness.verdict import report

ROOT = Path("/repo")


def diagnostic(severity: str, line: int = 0, message: str = "Bad") -> dict[str, object]:
    start = {"line": line, "character": 2}
    place = {"file": "/repo/src/a.py", "range": {"start": start}}
    return {**place, "severity": severity, "message": message, "rule": "reportAny"}


# #region Checker reports


def test_a_checker_report_that_is_not_one_reads_as_none() -> None:
    assert ruff_findings("ruff failed", ROOT) is None
    assert basedpyright_errors('Config file could not be parsed.\n{"version": 1}', ROOT) is None
    assert unformatted("{}", ROOT) is None


def test_a_finding_is_placed_one_based_from_the_repository_root() -> None:
    report_text = json.dumps({"generalDiagnostics": [diagnostic("error", 4, "Bad\n  because")]})
    [finding] = basedpyright_errors(report_text, ROOT) or []
    assert finding.text() == "src/a.py:5:3: reportAny Bad"
    assert finding.shown() == ["src/a.py:5:3: reportAny Bad", "    because"]
    assert finding.lines() == ["5:3: reportAny Bad", "    because"]


def test_a_type_warning_is_not_a_finding() -> None:
    report_text = json.dumps({"generalDiagnostics": [diagnostic("warning")]})
    assert basedpyright_errors(report_text, ROOT) == []


def test_a_syntax_error_is_named_though_ruff_gives_it_no_code() -> None:
    place = {"filename": "/repo/a.py", "location": {"row": 1, "column": 1}}
    found = ruff_findings(json.dumps([{**place, "code": None, "message": "SyntaxError"}]), ROOT)
    assert [finding.rule for finding in found or []] == ["syntax"]


def test_a_path_outside_the_repository_stays_whole() -> None:
    assert relative("/elsewhere/a.py", ROOT) == "/elsewhere/a.py"


def test_findings_are_listed_file_by_file_the_fullest_first() -> None:
    found = [
        Finding("b.py", 1, 1, "X", "one"),
        Finding("a.py", 1, 1, "X", "two"),
        Finding("a.py", 2, 1, "Y", ""),
    ]
    verdict = by_file(found, "finding")
    assert verdict.headline == "3 findings in 2 files"
    assert [section.title for section in verdict.sections] == ["a.py (2)", "b.py (1)"]
    assert verdict.counts == {"X": 2, "Y": 1}
    assert verdict.first[0] == "a.py:1:1: X two"


# #region Test and coverage reports


def test_a_test_report_that_cannot_be_read_reads_as_none(tmp_path: Path) -> None:
    assert failed_tests(tmp_path / "missing.json", tmp_path) is None
    (tmp_path / "tests.json").write_text('{"problems": []}')
    assert failed_tests(tmp_path / "tests.json", tmp_path) is None


def test_a_test_in_another_unit_is_named_from_the_repository_root(tmp_path: Path) -> None:
    problem = {"test": "tests/test_x.py::test_y", "message": "boom", "text": "trace"}
    recorded = {"rootdir": str(tmp_path / "libs" / "a"), "passed": 1, "problems": [problem]}
    (tmp_path / "tests.json").write_text(json.dumps(recorded))
    expected = (1, [Problem("libs/a/tests/test_x.py::test_y", "boom", "trace")])
    assert failed_tests(tmp_path / "tests.json", tmp_path) == expected


def test_a_coverage_report_that_cannot_be_read_reads_as_none(tmp_path: Path) -> None:
    assert coverage_gaps(tmp_path / "missing.json", tmp_path) is None
    (tmp_path / "coverage.json").write_text('{"totals": {"percent_covered": true}}')
    assert coverage_gaps(tmp_path / "coverage.json", tmp_path) is None


def test_coverage_gaps_name_the_lines_and_branches_no_test_runs(tmp_path: Path) -> None:
    missing = {"missing_lines": [3, 4, 5, 9], "missing_branches": [[2, 4], [7, -1]]}
    covered: dict[str, list[int]] = {"missing_lines": [], "missing_branches": []}
    files = {"src/a.py": missing, "src/b.py": covered}
    measured = {"totals": {"percent_covered": 87.5}, "files": files}
    (tmp_path / "coverage.json").write_text(json.dumps(measured))
    expected = (87.5, [Section("src/a.py", ["lines 3-5, 9", "branches 2->4, 7->exit"])])
    assert coverage_gaps(tmp_path / "coverage.json", tmp_path) == expected


# #region Verdicts and run records


def test_a_stage_run_alone_hands_its_verdict_to_no_one(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv(VERDICT_VARIABLE, raising=False)
    monkeypatch.chdir(tmp_path)
    report(Verdict(PASSED))
    assert list(tmp_path.iterdir()) == []


def test_a_verdict_arrives_whole(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    target = tmp_path / "lint.verdict"
    monkeypatch.setenv(VERDICT_VARIABLE, str(target))
    sections = [Section("a.py (1)", ["1:1: T201 x"])]
    parts = [Part("rule-a", FAILED, "1 finding")]
    first = ["a.py:1:1: T201 x"]
    verdict = Verdict(FAILED, "1 finding in 1 file", {"T201": 1}, first, sections, parts)
    report(verdict)
    assert read_verdict(target) == verdict


def test_a_file_that_holds_no_verdict_reads_as_none(tmp_path: Path) -> None:
    (tmp_path / "unknown").write_text('{"outcome": "great"}')
    (tmp_path / "garbled").write_text("not json")
    assert read_verdict(tmp_path / "unknown") is None
    assert read_verdict(tmp_path / "garbled") is None
    assert read_verdict(tmp_path / "missing") is None


def test_a_run_record_reads_back_whole(tmp_path: Path) -> None:
    parts = [Part("rule-a", PASSED, "skipped; nothing to check")]
    stage = StageRecord("doctor", PASSED, 1.5, "1 passed, 0 failed", {}, [], "d.log", None, parts)
    record = RunRecord("check", "2026-10-06T00:00:00+00:00", True, [stage], ["footer"])
    write_record(tmp_path, record)
    assert read_record(tmp_path) == record


def test_a_run_record_without_its_loop_reads_as_none(tmp_path: Path) -> None:
    (tmp_path / "run.json").write_text('{"stages": []}')
    assert read_record(tmp_path) is None
