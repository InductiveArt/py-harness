import re

import pytest

from py_harness.tables import read_table
from py_harness.tables import string_list
from py_harness.tables import subtable
from tests.support import REPOSITORY
from tests.support import Project

RUFF_CODE = re.compile(r"^\S+:\d+:\d+: (?P<code>[A-Z]+\d+) ", re.MULTILINE)
PYRIGHT_RULE = re.compile(r"^\S+:\d+:\d+: (?P<rule>report\w+) ", re.MULTILINE)


def ruff_codes(project: Project) -> set[str]:
    return {found["code"] for found in RUFF_CODE.finditer(project.make("lint").stdout)}


def pyright_rules(project: Project) -> set[str]:
    return {found["rule"] for found in PYRIGHT_RULE.finditer(project.make("typecheck").stdout)}


# #region Fix


def test_format_rejects_unformatted_code(project: Project) -> None:
    project.write("src/demo/shape.py", "x=1\n")
    result = project.make("format")
    assert result.returncode != 0
    assert "src/demo/shape.py" in result.stdout


def test_format_fix_rewrites_layout(project: Project) -> None:
    project.write("src/demo/shape.py", "x=1\n")
    assert project.make("format-fix").returncode == 0
    assert project.read("src/demo/shape.py") == "x = 1\n"


def test_lint_fix_repairs_what_it_safely_can(project: Project) -> None:
    project.write("src/demo/leftover.py", "import os\n\nx = 1\n")
    assert project.make("lint-fix").returncode == 0
    assert "import os" not in project.read("src/demo/leftover.py")


def test_lint_fix_rewrites_relative_imports_as_absolute(project: Project) -> None:
    project.write("src/demo/origin.py", "VALUE = 1\n")
    project.write("src/demo/user.py", "from .origin import VALUE\n\nDOUBLED = VALUE * 2\n")
    assert project.make("lint-fix").returncode == 0
    assert project.read("src/demo/user.py").startswith("from demo.origin import VALUE\n")


def test_lint_fix_passes_despite_findings_it_cannot_fix(project: Project) -> None:
    project.write("src/demo/loud.py", 'print("hello")\n')
    assert project.make("lint-fix").returncode == 0
    assert project.make("lint").returncode != 0


def test_lint_fix_orders_imports(project: Project) -> None:
    project.write("src/demo/order.py", "import sys\nimport os\n\npair = (os, sys)\n")
    assert project.make("lint-fix").returncode == 0
    assert project.read("src/demo/order.py").startswith("import os\nimport sys\n")


def test_lint_fix_puts_each_imported_symbol_on_its_own_line(project: Project) -> None:
    project.write("src/demo/joined.py", "from os import path, sep\n\npair = (path, sep)\n")
    assert project.make("lint-fix").returncode == 0
    assert project.read("src/demo/joined.py").startswith(
        "from os import path\nfrom os import sep\n"
    )


def test_lint_fix_unsafe_applies_what_lint_fix_leaves(project: Project) -> None:
    source = "def f() -> None:\n    unused = 1\n"
    project.write("src/demo/unsafe.py", source)
    project.make("lint-fix")
    assert project.read("src/demo/unsafe.py") == source
    project.make("lint-fix-unsafe")
    assert "unused" not in project.read("src/demo/unsafe.py")


# #region Lint


@pytest.mark.parametrize(
    ("source", "code"),
    [
        pytest.param('print("hello")\n', "T201", id="no-print"),
        pytest.param(
            'import logging\n\nname = "x"\nlogging.info(f"{name}")\n', "G004", id="structured-logs"
        ),
        pytest.param("# TODO: finish this\nx = 1\n", "FIX002", id="no-todo"),
        pytest.param(
            'import subprocess\n\nsubprocess.run("ls", shell=True)\n', "S602", id="security-scan"
        ),
        pytest.param("x = 1  # noqa: E501\n", "RUF100", id="no-unused-suppressions"),
        pytest.param("x = 1  # noqa\n", "PGH004", id="suppressions-name-their-rule"),
        pytest.param("import os\n", "F401", id="no-dead-code"),
        pytest.param("# x = compute(1)\ny = 1\n", "ERA001", id="no-commented-out-code"),
        pytest.param("from . import sibling\n", "TID252", id="absolute-imports"),
        pytest.param("from warnings import deprecated\n", "TID251", id="no-deprecated"),
        pytest.param(
            "import pytest\n\n\n@pytest.mark.skip\ndef test_x() -> None: ...\n",
            "TID251",
            id="no-skipped-tests",
        ),
        pytest.param(
            'import pytest\n\n\ndef test_x() -> None:\n    pytest.xfail("later")\n',
            "TID251",
            id="no-test-ending-itself",
        ),
        pytest.param("from typing import no_type_check\n", "TID251", id="no-unchecked-functions"),
    ],
)
def test_lint_enforces(project: Project, source: str, code: str) -> None:
    project.write("src/demo/planted.py", source)
    assert code in ruff_codes(project)


@pytest.mark.parametrize(
    "source",
    [
        pytest.param("def f(x: int) -> None:\n    assert x > 0\n", id="asserts"),
        pytest.param(
            'import subprocess\n\nsubprocess.run(["/bin/ls"], check=True)\n',
            id="shell-free-subprocess",
        ),
    ],
)
def test_lint_allows(project: Project, source: str) -> None:
    project.write("src/demo/allowed.py", source)
    assert ruff_codes(project) == set()


def test_lint_caps_complexity(project: Project) -> None:
    branches = "".join(f"    if x == {n}:\n        return {n}\n" for n in range(11))
    project.write("src/demo/branchy.py", f"def f(x: int) -> int:\n{branches}    return -1\n")
    assert "C901" in ruff_codes(project)


# #region Types


@pytest.mark.parametrize(
    ("source", "rule"),
    [
        pytest.param(
            "def f(x):\n    return x\n", "reportMissingParameterType", id="typecheck-strict"
        ),
        pytest.param(
            "x: int = 1  # type: ignore[assignment]\n",
            "reportUnnecessaryTypeIgnoreComment",
            id="no-unused-suppressions",
        ),
        pytest.param(
            "x: int = 1  # type: ignore\n",
            "reportIgnoreCommentWithoutRule",
            id="suppressions-name-their-rule",
        ),
        pytest.param(
            "def _helper() -> None:\n    pass\n", "reportUnusedFunction", id="no-dead-code"
        ),
        pytest.param(
            "from typing import Any\n\n\ndef f(x: Any) -> None: ...\n",
            "reportExplicitAny",
            id="no-explicit-any",
        ),
        pytest.param('import json\n\nvalue = json.loads("1")\n', "reportAny", id="no-silent-any"),
    ],
)
def test_typecheck_enforces(project: Project, source: str, rule: str) -> None:
    project.write("src/demo/planted.py", source)
    assert rule in pyright_rules(project)


def test_typecheck_rejects_an_implicit_reexport(project: Project) -> None:
    project.write("src/demo/origin.py", "def thing() -> int:\n    return 1\n")
    project.write("src/demo/passer.py", "from demo.origin import thing\n\nvalue = thing()\n")
    project.write("src/demo/consumer.py", "from demo.passer import thing\n\nvalue = thing()\n")
    assert "reportPrivateLocalImportUsage" in pyright_rules(project)


def test_only_the_checkers_that_decide_a_verdict_are_pinned_exactly() -> None:
    project = subtable(read_table(REPOSITORY / "pyproject.toml"), "project")
    exact = sorted(
        spec.split("==")[0] for spec in string_list(project, "dependencies") if "==" in spec
    )
    ranged = [spec for spec in string_list(project, "dependencies") if "==" not in spec]
    assert exact == ["basedpyright", "ruff"]
    assert all(">=" in spec and ",<" in spec for spec in ranged)
