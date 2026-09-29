from pathlib import Path

import pytest

from py_harness.suppressions import inventory
from py_harness.suppressions import suppressions_in_text
from py_harness.suppressions import tally
from tests.support import Project

PYRIGHT_TWO = "x = 1  # pyright: ignore[reportAny, reportUnknownVariableType]\n"
XFAIL = "import pytest\n\n\n@pytest.mark.xfail\ndef test_x() -> None: ...\n"


def labels(path: str, text: str) -> list[str]:
    return [found.label for found in suppressions_in_text(path, text)]


def repository(root: Path) -> Project:
    project = Project(root)
    project.write("src/app.py", "x = 1\n")
    project.git("init", "-q")
    project.commit()
    return project


# #region Reading one file


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        pytest.param(
            "x = 1  # noqa: E501, BLE001\n", ["noqa E501", "noqa BLE001"], id="noqa-codes"
        ),
        pytest.param("x = 1  # noqa\n", ["noqa (every rule)"], id="bare-noqa"),
        pytest.param(
            PYRIGHT_TWO, ["pyright reportAny", "pyright reportUnknownVariableType"], id="pyright"
        ),
        pytest.param(
            "x = 1  # type: ignore[attr-defined]\n", ["type attr-defined"], id="type-ignore"
        ),
        pytest.param("x = 1  # type: ignore\n", ["type (every rule)"], id="bare-type-ignore"),
        pytest.param('KEY = "k"  # pragma: allowlist secret\n', ["allowlist secret"], id="pragma"),
        pytest.param(XFAIL, ["xfail"], id="xfail"),
        pytest.param("from typing import cast\n\nx = cast(int, 1)\n", ["cast"], id="cast"),
        pytest.param("import typing\n\nx = typing.cast(int, 1)\n", ["cast"], id="typing-cast"),
        pytest.param("x = shape.cast(int)\ny = int(1)\n", [], id="other-calls"),
        pytest.param('TEXT = "# noqa: E501"\n', [], id="inside-a-string"),
        pytest.param("x = (  # noqa: E501\n", ["noqa E501"], id="does-not-tokenize"),
    ],
)
def test_a_python_file_counts_each_bypassed_rule(text: str, expected: list[str]) -> None:
    assert labels("src/app.py", text) == expected


def test_another_file_counts_a_pragma_only_where_it_ends_its_line() -> None:
    text = "key: k  # pragma: allowlist secret\nMention `# pragma: allowlist secret` here.\n"
    assert labels("config.yaml", text) == ["allowlist secret"]


def test_each_excluded_unit_counts() -> None:
    assert labels(".py-harness/ignore", "# retired\nlibs/old  # kept\n") == ["excluded unit"]


def test_the_inventory_reads_the_whole_repository(tmp_path: Path) -> None:
    project = Project(tmp_path)
    project.write("src/app.py", "x = 1  # noqa: E501\n")
    project.write("docs/notes.yaml", "key: k  # pragma: allowlist secret\n")
    project.write(".py-harness/ignore", "libs/old\n")
    found = sorted(suppression.label for suppression in inventory(project.root))
    assert found == ["allowlist secret", "excluded unit", "noqa E501"]


def test_a_file_that_is_not_text_is_skipped(tmp_path: Path) -> None:
    (tmp_path / "blob.bin").write_bytes(bytes([0xFF, 0xFE, 0x00]))
    assert inventory(tmp_path) == []


# #region Comparing with the last commit


def test_adding_a_suppression_counts_plus_one(tmp_path: Path) -> None:
    project = repository(tmp_path)
    project.write("src/app.py", "x = 1\ny = 2  # noqa: E501\n")
    counted = tally(project.root)
    added = [(found.path, found.line, found.label) for found in counted.added or []]
    assert (counted.change, added) == (1, [("src/app.py", 2, "noqa E501")])


def test_a_suppression_in_an_untouched_file_is_not_added(tmp_path: Path) -> None:
    project = repository(tmp_path)
    project.write("src/app.py", "x = 1  # noqa: E501\n")
    project.commit()
    project.write("src/other.py", "y = 2\n")
    counted = tally(project.root)
    assert (counted.change, counted.added, len(counted.present)) == (0, [], 1)


def test_removing_a_suppression_counts_minus_one(tmp_path: Path) -> None:
    project = repository(tmp_path)
    project.write("src/app.py", "x = 1  # noqa: E501\n")
    project.commit()
    project.write("src/app.py", "x = 1\n")
    assert tally(project.root).change == -1


def test_editing_a_line_that_keeps_its_suppression_counts_zero(tmp_path: Path) -> None:
    project = repository(tmp_path)
    project.write("src/app.py", "x = 1  # noqa: E501\n")
    project.commit()
    project.write("src/app.py", "x = 2  # noqa: E501\n")
    counted = tally(project.root)
    assert (counted.change, len(counted.present)) == (0, 1)


def test_every_suppression_in_an_untracked_file_is_added(tmp_path: Path) -> None:
    project = repository(tmp_path)
    project.write("src/new.py", "a = 1  # pyright: ignore[reportAny]\n")
    assert [found.label for found in tally(project.root).added or []] == ["pyright reportAny"]


def test_a_deleted_file_takes_its_suppressions_with_it(tmp_path: Path) -> None:
    project = repository(tmp_path)
    project.write("src/app.py", "x = 1  # noqa: E501\n")
    project.commit()
    (project.root / "src" / "app.py").unlink()
    assert tally(project.root).change == -1


def test_without_a_commit_the_change_is_unknown(tmp_path: Path) -> None:
    project = Project(tmp_path)
    project.write("src/app.py", "x = 1  # noqa: E501\n")
    project.git("init", "-q")
    counted = tally(project.root)
    assert (counted.change, counted.added, len(counted.present)) == (None, None, 1)
