import shutil

import pytest

from tests.support import DECLARED_LAYOUT
from tests.support import Project
from tests.support import declare_layout
from tests.support import manifest

# Split, so this file carries no literal key; the name alone still reads as a secret.
SECRET = "AKIA" + "IOSFODNN7EXAMPLE"  # pragma: allowlist secret
# Split, so neither of these is a pragma itself.
PRAGMA = "pragma: " + "allowlist secret"
NEXTLINE_PRAGMA = "pragma: " + "allowlist nextline secret"


# #region no-reexport


@pytest.mark.parametrize(
    ("source", "finding"),
    [
        pytest.param("import os as os\n", "1  `os as os`", id="import-as-itself"),
        pytest.param(
            "from os import path as path\n", "1  `path as path`", id="from-import-as-itself"
        ),
        pytest.param(
            'from os import path\n\n__all__ = ["path"]\n', "3  `__all__` lists `path`", id="all"
        ),
        pytest.param(
            'from os import path\n\n__all__: list[str] = ["path"]\n',
            "3  `__all__` lists `path`",
            id="annotated-all",
        ),
        pytest.param(
            'from os import path\n\n__all__ = []\n__all__ += ["path"]\n',
            "4  `__all__` lists `path`",
            id="all-added",
        ),
        pytest.param(
            'from os import path\n\n__all__ = []\n__all__.extend(["path"])\n',
            "4  `__all__` lists `path`",
            id="all-extended",
        ),
        pytest.param(
            'from os import path\n\n__all__ = []\n__all__.append("path")\n',
            "4  `__all__` lists `path`",
            id="all-appended",
        ),
        pytest.param(
            'import os.path\n\n__all__ = ["os"]\n', "3  `__all__` lists `os`", id="dotted-import"
        ),
        pytest.param(
            "import os.path\n\nsep = os.path.sep\n",
            "3  `sep = os.path.sep` makes an imported name importable from here",
            id="assigned-from-an-import",
        ),
        pytest.param(
            'names = ["x"]\n__all__ = names\n',
            "2  `__all__` is built from something other than",
            id="computed-all",
        ),
    ],
)
def test_no_reexport_rejects(project: Project, source: str, finding: str) -> None:
    project.write("src/demo/door.py", source)
    result = project.make("doctor", "RULE=no-reexport")
    assert result.returncode != 0
    assert f"src/demo/door.py:{finding}" in result.stdout


def test_no_reexport_accepts_a_module_exporting_its_own_definitions(project: Project) -> None:
    source = (
        "import os as operating\n\n\n"
        "def f() -> str:\n    return operating.sep\n\n\n"
        '__all__ = ["f"]\n'
    )
    project.write("src/demo/own.py", source)
    assert "doctor: no-reexport OK" in project.make("doctor", "RULE=no-reexport").stdout


def test_no_reexport_accepts_a_private_alias_and_a_computed_value(project: Project) -> None:
    source = "import os\n\n_sep = os.sep\nroot = os.getcwd()\nLIMIT: int = 3\nCOUNT: int\n"
    project.write("src/demo/own.py", source)
    assert "doctor: no-reexport OK" in project.make("doctor", "RULE=no-reexport").stdout


# #region no-hidden-names


@pytest.mark.parametrize(
    ("source", "finding"),
    [
        pytest.param("import json as serial\n", "1  `json as serial`", id="module-renamed"),
        pytest.param("from os import path as where\n", "1  `path as where`", id="symbol-renamed"),
        pytest.param(
            "def f(o: object, name: str) -> bool:\n    return hasattr(o, name)\n",
            "2  `hasattr` with a computed name",
            id="computed-attribute",
        ),
        pytest.param(
            'import importlib\n\nimportlib.import_module("os")\n',
            "3  `importlib.import_module`",
            id="import-module",
        ),
        pytest.param(
            "from importlib import import_module\n",
            "1  `importlib.import_module`",
            id="import-module-imported",
        ),
        pytest.param('__import__("os")\n', "1  `__import__`", id="dunder-import"),
        pytest.param("names = globals()\n", "1  `globals()`", id="namespace-lookup"),
        pytest.param(
            "def __getattr__(name: str) -> str:\n    return name\n",
            "1  `def __getattr__`",
            id="module-fallback",
        ),
        pytest.param(
            "class Proxy:\n    async def __getattribute__(self, name: str) -> str:\n"
            "        return name\n",
            "2  `def __getattribute__`",
            id="class-fallback",
        ),
    ],
)
def test_no_hidden_names_rejects(project: Project, source: str, finding: str) -> None:
    project.write("src/demo/hidden.py", source)
    result = project.make("doctor", "RULE=no-hidden-names")
    assert result.returncode != 0
    assert f"src/demo/hidden.py:{finding}" in result.stdout


@pytest.mark.parametrize(
    "source",
    [
        pytest.param("import numpy as np\n", id="conventional-alias"),
        pytest.param("import os.path as path\n", id="submodule-under-its-own-name"),
        pytest.param("import os as os\nfrom os import sep as sep\n", id="same-name-alias"),
        pytest.param("from importlib import util\n", id="other-importlib-member"),
        pytest.param(
            'def f(o: object) -> bool:\n    return hasattr(o, "name")\n', id="literal-attribute"
        ),
    ],
)
def test_no_hidden_names_accepts(project: Project, source: str) -> None:
    project.write("src/demo/plain.py", source)
    assert "doctor: no-hidden-names OK" in project.make("doctor", "RULE=no-hidden-names").stdout


def test_no_hidden_names_reads_tests_too(project: Project) -> None:
    project.write("tests/test_hidden.py", "import json as serial\n")
    result = project.make("doctor", "RULE=no-hidden-names")
    assert "tests/test_hidden.py:1  `json as serial`" in result.stdout


@pytest.mark.parametrize(
    ("rule", "source", "finding"),
    [
        pytest.param(
            "no-hidden-names", "import json as serial\n", "1  `json as serial`", id="hidden-names"
        ),
        pytest.param(
            "no-comment-overreach",
            "# Calls run_elsewhere.\nVALUE = 1\n",
            "1  run_elsewhere",
            id="comment-overreach",
        ),
    ],
)
def test_a_name_rule_reads_python_outside_source_and_tests(
    project: Project, rule: str, source: str, finding: str
) -> None:
    project.write("scripts/tool.py", source)
    assert f"scripts/tool.py:{finding}" in project.make("doctor", f"RULE={rule}").stdout


# #region no-cycles


def cycle_report(project: Project) -> str:
    result = project.make("doctor", "RULE=no-cycles")
    assert result.returncode != 0
    return result.stdout


def test_no_cycles_reports_each_edge_of_a_cycle_with_its_lines(project: Project) -> None:
    project.write("src/demo/a.py", "import demo.b\n")
    project.write("src/demo/b.py", "VALUE = 1\nimport demo.a\n")
    report = cycle_report(project)
    assert "  cycle in .:\n    demo.a -> demo.b (line 1)\n    demo.b -> demo.a (line 2)\n" in report


def test_no_cycles_finds_a_cycle_through_three_modules(project: Project) -> None:
    project.write("src/demo/a.py", "import demo.b\n")
    project.write("src/demo/b.py", "import demo.c\n")
    project.write("src/demo/c.py", "import demo.a\n")
    report = cycle_report(project)
    assert "demo.c -> demo.a (line 1)" in report


def test_no_cycles_reports_independent_cycles_separately(project: Project) -> None:
    for first, second in (("a", "b"), ("c", "d")):
        project.write(f"src/demo/{first}.py", f"import demo.{second}\n")
        project.write(f"src/demo/{second}.py", f"import demo.{first}\n")
    assert cycle_report(project).count("cycle in .:") == 2


def test_a_function_level_import_still_closes_a_cycle(project: Project) -> None:
    project.write("src/demo/a.py", "import demo.b\n")
    project.write("src/demo/b.py", "def later() -> None:\n    import demo.a\n")
    assert "demo.b -> demo.a (line 2)" in cycle_report(project)


def test_a_package_and_a_child_importing_each_other_is_a_cycle(project: Project) -> None:
    project.write("src/demo/__init__.py", "from demo.child import value\n")
    project.write("src/demo/child.py", "import demo\n\nvalue = 1\n")
    assert "demo -> demo.child (line 1)" in cycle_report(project)


def test_imports_under_type_checking_are_exempt(project: Project) -> None:
    project.write("src/demo/a.py", "import demo.b\n")
    project.write(
        "src/demo/b.py",
        "from typing import TYPE_CHECKING\n\nif TYPE_CHECKING:\n    import demo.a\n",
    )
    assert "doctor: no-cycles OK" in project.make("doctor", "RULE=no-cycles").stdout


def test_an_acyclic_diamond_passes(project: Project) -> None:
    project.write("src/demo/a.py", "import demo.b\nimport demo.c\n")
    project.write("src/demo/b.py", "")
    project.write("src/demo/c.py", "import demo.b\n")
    assert "doctor: no-cycles OK" in project.make("doctor", "RULE=no-cycles").stdout


# #region boundaries

LAYERS = """
[tool.importlinter]
root_package = "demo"

[[tool.importlinter.contracts]]
name = "layers"
type = "layers"
layers = ["demo.high", "demo.low"]
"""


def test_boundaries_is_skipped_without_contracts(project: Project) -> None:
    result = project.make("doctor", "RULE=boundaries")
    assert result.returncode == 0
    assert (
        "doctor: boundaries skipped; pyproject.toml declares no [tool.importlinter] contracts"
        in result.stdout
    )


def test_boundaries_fails_a_broken_contract(project: Project) -> None:
    project.write("pyproject.toml", manifest() + LAYERS)
    project.write("src/demo/high.py", "")
    project.write("src/demo/low.py", "import demo.high\n")
    result = project.make("doctor", "RULE=boundaries")
    assert result.returncode != 0
    assert "demo.low is not allowed to import demo.high" in result.stdout


def test_boundaries_passes_a_kept_contract(project: Project) -> None:
    project.write("pyproject.toml", manifest() + LAYERS)
    project.write("src/demo/high.py", "import demo.low\n")
    project.write("src/demo/low.py", "")
    assert "doctor: boundaries OK" in project.make("doctor", "RULE=boundaries").stdout


# #region no-secrets


def secrets_report(project: Project) -> str:
    return project.make("doctor", "RULE=no-secrets").stdout


def test_an_untracked_secret_fails(project: Project) -> None:
    project.write("config.py", f'KEY = "{SECRET}"\n')
    assert "config.py:1  AWS Access Key" in secrets_report(project)


def test_a_staged_secret_fails(project: Project) -> None:
    project.write("config.py", f'KEY = "{SECRET}"\n')
    project.git("add", "config.py")
    assert "config.py:1  AWS Access Key" in secrets_report(project)


def test_a_pragma_allows_the_secret_on_its_line(project: Project) -> None:
    project.write("config.py", f'KEY = "{SECRET}"  # {PRAGMA}\n')
    assert "doctor: no-secrets OK" in secrets_report(project)


def test_a_nextline_pragma_allows_the_line_below(project: Project) -> None:
    project.write("config.py", f'# {NEXTLINE_PRAGMA}\nKEY = "{SECRET}"\n')
    assert "doctor: no-secrets OK" in secrets_report(project)


def test_a_pragma_that_allows_nothing_fails(project: Project) -> None:
    project.write("config.py", f'NAME = "plain"  # {PRAGMA}\n')
    assert "config.py:1  allowlist pragma allows nothing" in secrets_report(project)


def test_a_pragma_mentioned_mid_line_is_not_a_pragma(project: Project) -> None:
    project.write("NOTES.md", f"End a line with `# {PRAGMA}` to allow a lookalike.\n")
    assert "doctor: no-secrets OK" in secrets_report(project)


def test_the_lockfile_is_not_scanned(project: Project) -> None:
    project.write("uv.lock", f'hash = "{SECRET}"\n')
    assert "doctor: no-secrets OK" in secrets_report(project)


def test_a_gitignored_file_is_not_scanned(project: Project) -> None:
    project.write(".gitignore", "local.env\n")
    project.write("local.env", f"KEY={SECRET}\n")
    assert "doctor: no-secrets OK" in secrets_report(project)


def test_no_secrets_fails_outside_a_git_repository(project: Project) -> None:
    shutil.rmtree(project.root / ".git")
    result = project.make("doctor", "RULE=no-secrets")
    assert result.returncode != 0
    assert "Secrets not scanned (forbidden):\n  not a git repository" in result.stdout


# #region no-comment-overreach


@pytest.mark.parametrize(
    ("source", "finding"),
    [
        pytest.param("# Mirrors helper_function.\nx = 1\n", "1  helper_function", id="snake-case"),
        pytest.param("# Reads config.settings first.\nx = 1\n", "1  config.settings", id="dotted"),
        pytest.param("# Owned by `Registry`.\nx = 1\n", "1  Registry", id="backticked"),
        pytest.param("# Bounded by MAX_RETRIES.\nx = 1\n", "1  MAX_RETRIES", id="upper-snake"),
        pytest.param(
            'def f() -> None:\n    """Called by run_all."""\n', "2  run_all", id="docstring"
        ),
    ],
)
def test_no_comment_overreach_rejects_a_name_out_of_reach(
    project: Project, source: str, finding: str
) -> None:
    project.write("src/demo/lonely.py", source)
    result = project.make("doctor", "RULE=no-comment-overreach")
    assert result.returncode != 0
    assert f"src/demo/lonely.py:{finding}" in result.stdout


@pytest.mark.parametrize(
    "source",
    [
        pytest.param(
            "def helper_function() -> None: ...\n\n\n# helper_function stays pure.\n", id="defined"
        ),
        pytest.param("import os.path\n\n# os.path joins here.\nx = os.path.sep\n", id="imported"),
        pytest.param(
            'NAME = "max_retries"\n# max_retries comes from the environment.\n', id="quoted"
        ),
        pytest.param("# sub_module keeps no state.\nx = 1\n", id="own-module"),
        pytest.param("# Runs before JavaScript loads.\nx = 1\n", id="prose-capitals"),
        pytest.param("# Follows https://example.com/spec_v2 exactly.\nx = 1\n", id="url"),
        pytest.param("# Small values, e.g. zero.\nx = 1\n", id="abbreviation"),
        pytest.param("# Some servers reject `think: false`.\nthink = 1\n", id="data-literal"),
    ],
)
def test_no_comment_overreach_accepts(project: Project, source: str) -> None:
    project.write("src/demo/sub_module.py", source)
    assert (
        "doctor: no-comment-overreach OK"
        in project.make("doctor", "RULE=no-comment-overreach").stdout
    )


def test_no_comment_overreach_reads_tests_too(project: Project) -> None:
    project.write("tests/test_x.py", "# Same as test_elsewhere.\ndef test_x() -> None:\n    pass\n")
    assert (
        "tests/test_x.py:1  test_elsewhere"
        in project.make("doctor", "RULE=no-comment-overreach").stdout
    )


# #region Declared layout


@pytest.mark.parametrize(
    ("rule", "files", "finding"),
    [
        pytest.param(
            "no-cycles",
            {"a.py": "import demo.b\n", "b.py": "import demo.a\n"},
            "demo.a -> demo.b (line 1)",
            id="no-cycles",
        ),
        pytest.param(
            "no-reexport", {"door.py": "import os as os\n"}, "`os as os`", id="no-reexport"
        ),
        pytest.param(
            "no-hidden-names",
            {"hidden.py": "import json as serial\n"},
            "`json as serial`",
            id="no-hidden-names",
        ),
        pytest.param(
            "boundaries",
            {"high.py": "", "low.py": "import demo.high\n"},
            "demo.low is not allowed to import demo.high",
            id="boundaries",
        ),
    ],
)
def test_source_rules_read_the_declared_source(
    project: Project, rule: str, files: dict[str, str], finding: str
) -> None:
    declare_layout(project)
    project.write("pyproject.toml", manifest() + DECLARED_LAYOUT + LAYERS)
    for name, source in files.items():
        project.write(f"src/main/demo/{name}", source)
    assert finding in project.make("doctor", f"RULE={rule}").stdout


def test_no_comment_overreach_reads_the_declared_suite(project: Project) -> None:
    declare_layout(project)
    project.write(
        "src/test/test_x.py", "# Same as test_elsewhere.\ndef test_x() -> None:\n    pass\n"
    )
    report = project.make("doctor", "RULE=no-comment-overreach").stdout
    assert "src/test/test_x.py:1  test_elsewhere" in report


# #region no-blanket-exemptions


@pytest.mark.parametrize(
    ("source", "kind"),
    [
        pytest.param(
            "# pyright: basic\nx = 1\n", "a type-checking directive for the whole file", id="mode"
        ),
        pytest.param(
            "# pyright: reportAny=false\nx = 1\n",
            "a type-checking directive for the whole file",
            id="rule-off",
        ),
        pytest.param(
            "# type: ignore\nx = 1\n",
            "a type-checking exemption for the whole file",
            id="type-ignore",
        ),
        pytest.param(
            "# ruff: noqa: T201\nx = 1\n", "a lint exemption for the whole file", id="ruff-noqa"
        ),
        pytest.param(
            "# flake8: noqa\nx = 1\n", "a lint exemption for the whole file", id="flake8-noqa"
        ),
        pytest.param("# fmt: off\nx = 1\n", "a formatting exemption", id="fmt-off"),
        pytest.param("x = 1  # fmt: skip\n", "a formatting exemption", id="fmt-skip"),
        pytest.param(
            "# isort: skip_file\nimport os\n", "an import-sorting exemption", id="isort-skip-file"
        ),
    ],
)
def test_no_blanket_exemptions_rejects(project: Project, source: str, kind: str) -> None:
    project.write("scripts/tool.py", source)
    result = project.make("doctor", "RULE=no-blanket-exemptions")
    assert result.returncode != 0
    assert f"scripts/tool.py:1  {kind}" in result.stdout


@pytest.mark.parametrize(
    "source",
    [
        pytest.param(
            "x: int = 1  # pyright: ignore[reportAssignmentType]\n", id="named-pyright-ignore"
        ),
        pytest.param("x: int = 1  # type: ignore[assignment]\n", id="trailing-type-ignore"),
        pytest.param("x = 1  # noqa: E501\n", id="line-noqa"),
        pytest.param('TEXT = "# pyright: basic"\n', id="inside-a-string"),
    ],
)
def test_no_blanket_exemptions_accepts_line_level_suppressions(
    project: Project, source: str
) -> None:
    project.write("scripts/tool.py", source)
    report = project.make("doctor", "RULE=no-blanket-exemptions").stdout
    assert "doctor: no-blanket-exemptions OK" in report


# #region report-suppressions


def test_report_suppressions_counts_each_rule_and_file(project: Project) -> None:
    project.write("src/demo/a.py", "x = 1  # noqa: E501\ny = 2  # noqa: E501\n")
    report = project.make("doctor", "RULE=suppressions")
    assert report.returncode == 0
    assert "Suppressions in the repository (2, 1000.0 per 1,000 lines of Python):" in report.stdout
    assert "     2  noqa E501\nFiles holding the most:\n     2  src/demo/a.py" in report.stdout


def test_report_suppressions_without_python_gives_no_density(project: Project) -> None:
    project.write("config.yaml", "key: k  # pragma: allowlist secret\n")
    assert (
        "(1, no lines of Python to compare with)"
        in project.make("doctor", "RULE=suppressions").stdout
    )


# #region report-shared-names


def test_report_shared_names_lists_each_name_with_its_locations(project: Project) -> None:
    every_kind = (
        "def load() -> None: ...\n\n\n"
        "async def fetch() -> None: ...\n\n\n"
        "class Store: ...\n\n\n"
        "LIMIT = 1\n"
        "SIZE: int = 2\n"
    )
    project.write("src/demo/one.py", every_kind)
    project.write("tests/test_two.py", every_kind)
    report = project.make("doctor", "RULE=shared-names")
    assert report.returncode == 0
    assert "Names defined in more than one module (5):" in report.stdout
    for name, line in (("LIMIT", 10), ("SIZE", 11), ("Store", 7), ("fetch", 4), ("load", 1)):
        assert f"  {name}  src/demo/one.py:{line}, tests/test_two.py:{line}\n" in report.stdout


def test_report_shared_names_skips_private_names_and_conventions(project: Project) -> None:
    source = (
        "import os\n\n\n"
        "def main() -> None: ...\n\n\n"
        "_cache: dict[str, str] = {}\n"
        "pytestmark: list[str] = []\n"
        'os.environ["MODE"] = "test"\n'
    )
    project.write("src/demo/one.py", source)
    project.write("src/demo/two.py", source)
    assert "doctor: shared-names none" in project.make("doctor", "RULE=shared-names").stdout


def test_report_shared_names_reads_python_outside_source_and_tests(project: Project) -> None:
    project.write("scripts/one.py", "LIMIT = 1\n")
    project.write("scripts/two.py", "LIMIT = 2\n")
    report = project.make("doctor", "RULE=shared-names").stdout
    assert "  LIMIT  scripts/one.py:1, scripts/two.py:1\n" in report
