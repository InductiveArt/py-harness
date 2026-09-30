from pathlib import Path

import pytest

from py_harness import stage
from tests.support import PASSING
from tests.support import SHARE
from tests.support import Project
from tests.support import declare_layout
from tests.support import manifest

FAILING = "def test_fails() -> None:\n    assert False\n"
MARKED = "import pytest\n\npytestmark = pytest.mark.integration\n\n\n"
IMPORTABLE = '\n[tool.pytest.ini_options]\npythonpath = ["src"]\n'


def wired_workspace(root: Path, *members: str) -> Project:
    created = Project(root)
    created.write("Makefile", f"include {SHARE / 'harness.mk'}\n")
    root_table = (
        f"[tool.uv.workspace]\nmembers = {list(members)!r}\n\n"
        f'[tool.ruff]\nextend = "{SHARE / "ruff.toml"}"\n\n'
        f'[tool.basedpyright]\nextends = "{SHARE / "pyrightconfig.json"}"\n'
    )
    created.write("pyproject.toml", root_table)
    for path in members:
        name = Path(path).name
        created.write(f"{path}/pyproject.toml", f'[project]\nname = "{name}"\nversion = "0.0.0"\n')
        created.write(f"{path}/src/{name.replace('-', '_')}/__init__.py")
    return created


def importable(project: Project) -> None:
    project.write("pyproject.toml", manifest() + IMPORTABLE)


# #region Test


def test_test_runs_the_tests_outside_integration(project: Project) -> None:
    project.write("tests/test_unit.py", PASSING)
    project.write("tests/test_slow.py", MARKED + FAILING)
    result = project.make("test")
    assert result.returncode == 0
    assert "→ test ." in result.stdout


def test_test_files_may_share_a_name_in_different_folders(project: Project) -> None:
    project.write("tests/unit/test_same.py", PASSING)
    project.write("tests/integration/test_same.py", PASSING)
    result = project.make("test")
    assert result.returncode == 0, result.stdout
    assert "2 passed" in result.stdout


def test_each_units_line_starts_a_paragraph(project: Project) -> None:
    project.write("tests/test_unit.py", PASSING)
    assert project.make("test").stdout.startswith("\n→ test .\n")


def test_test_fails_on_a_failing_test(project: Project) -> None:
    project.write("tests/test_unit.py", FAILING)
    result = project.make("test")
    assert result.returncode != 0
    assert "FAILED tests/test_unit.py::test_fails" in result.stdout


def test_test_skips_a_unit_without_tests_and_says_so(project: Project) -> None:
    result = project.make("test")
    assert result.returncode == 0
    assert "·  skip test . (no tests at tests)" in result.stdout


def test_test_stops_at_the_first_failing_unit(tmp_path: Path) -> None:
    created = wired_workspace(tmp_path, "libs/a", "libs/b")
    created.write("libs/a/tests/test_a.py", FAILING)
    created.write("libs/b/tests/test_b.py", PASSING)
    result = created.make("test")
    assert result.returncode != 0
    assert "→ test libs/b" not in result.stdout


def test_pytest_rejects_an_unregistered_marker(project: Project) -> None:
    project.write("tests/test_unit.py", "import pytest\n\n\n@pytest.mark.slowish\n" + PASSING)
    assert project.make("test").returncode != 0


def test_an_xfail_that_passes_fails(project: Project) -> None:
    project.write("tests/test_unit.py", "import pytest\n\n\n@pytest.mark.xfail\n" + PASSING)
    assert project.make("test").returncode != 0


@pytest.mark.parametrize("setting", ["strict", "run"])
def test_an_xfail_that_cannot_prove_its_test_fails_is_refused(
    project: Project, setting: str
) -> None:
    marker = f"import pytest\n\n\n@pytest.mark.xfail({setting}=False)\n"
    project.write("tests/test_unit.py", marker + PASSING)
    result = project.make("test")
    assert result.returncode != 0
    assert f"tests/test_unit.py::test_passes ({setting}=False)" in result.stderr + result.stdout


def test_the_callers_pytest_arguments_are_dropped(project: Project) -> None:
    project.write("tests/test_unit.py", FAILING)
    deselecting = {"PYTEST_ADDOPTS": "--deselect tests/test_unit.py"}
    assert project.make("test", environment=deselecting).returncode != 0


def test_a_stage_reports_units_it_cannot_resolve(tmp_path: Path) -> None:
    created = wired_workspace(tmp_path)
    result = created.make("test")
    assert result.returncode != 0
    assert "units: pyproject.toml declares no [project]" in result.stderr


def test_an_unknown_stage_is_refused(capsys: pytest.CaptureFixture[str]) -> None:
    assert stage.main(["stage", str(SHARE), "lint"]) == 2
    assert "unknown stage: lint (expected test, test-integration, coverage, typecheck)" in (
        capsys.readouterr().err
    )


# #region Integration


def test_test_integration_runs_only_integration_tests(project: Project) -> None:
    project.write("tests/test_unit.py", FAILING)
    project.write("tests/test_slow.py", MARKED + PASSING)
    result = project.make("test-integration")
    assert result.returncode == 0
    assert "1 passed, 1 deselected" in result.stdout


def test_test_integration_passes_when_no_test_is_marked(project: Project) -> None:
    project.write("tests/test_unit.py", PASSING)
    result = project.make("test-integration")
    assert result.returncode == 0
    assert "·  skip test-integration . (no test is marked integration)" in result.stdout


def test_test_passes_when_every_test_is_marked_integration(project: Project) -> None:
    project.write("tests/test_slow.py", MARKED + PASSING)
    result = project.make("test")
    assert result.returncode == 0
    assert "·  skip test . (every test is marked integration)" in result.stdout


# #region One unit


def test_test_pkg_runs_only_the_named_unit(tmp_path: Path) -> None:
    created = wired_workspace(tmp_path, "libs/a", "libs/b")
    created.write("libs/a/tests/test_a.py", FAILING)
    created.write("libs/b/tests/test_b.py", PASSING)
    result = created.make("test-pkg", "PKG=b")
    assert result.returncode == 0
    assert "→ test libs/a" not in result.stdout


def test_a_unit_is_found_by_its_normalized_name(tmp_path: Path) -> None:
    created = wired_workspace(tmp_path, "libs/lib-b")
    created.write("libs/lib-b/tests/test_b.py", PASSING)
    assert "→ test libs/lib-b" in created.make("test-pkg", "PKG=Lib_B").stdout


def test_a_pkg_target_requires_pkg(project: Project) -> None:
    result = project.make("test-pkg")
    assert result.returncode != 0
    assert "PKG=<project name> is required" in result.stderr


def test_an_unknown_package_lists_the_available_ones(project: Project) -> None:
    result = project.make("test-pkg", "PKG=nope")
    assert result.returncode != 0
    assert "unknown package: nope\navailable:\n  demo  (.)" in result.stdout


def test_typecheck_pkg_checks_only_the_named_unit(tmp_path: Path) -> None:
    created = wired_workspace(tmp_path, "libs/a", "libs/b")
    created.write("libs/a/src/a/broken.py", "def f(x):\n    return x\n")
    assert created.make("typecheck-pkg", "PKG=b").returncode == 0
    assert created.make("typecheck-pkg", "PKG=a").returncode != 0


# #region Coverage

HALF_TESTED = "def used() -> int:\n    return 1\n\n\ndef unused() -> int:\n    return 2\n"
TESTS_USED = "from demo.calc import used\n\n\ndef test_used() -> None:\n    assert used() == 1\n"


def test_coverage_fails_below_full_branch_coverage(project: Project) -> None:
    importable(project)
    project.write("src/demo/calc.py", HALF_TESTED)
    project.write("tests/test_calc.py", TESTS_USED)
    result = project.make("coverage")
    assert result.returncode != 0
    assert "FAIL Required test coverage of 100.0% not reached" in result.stdout


def test_coverage_passes_at_full_branch_coverage(project: Project) -> None:
    importable(project)
    project.write("src/demo/calc.py", "def used() -> int:\n    return 1\n")
    project.write("tests/test_calc.py", TESTS_USED)
    assert project.make("coverage").returncode == 0


def test_a_no_cover_pragma_excludes_nothing(project: Project) -> None:
    importable(project)
    project.write(
        "src/demo/calc.py",
        HALF_TESTED.replace("def unused() -> int:", "def unused() -> int:  # pragma: no cover"),
    )
    project.write("tests/test_calc.py", TESTS_USED)
    assert project.make("coverage").returncode != 0


@pytest.mark.parametrize(
    "block",
    [
        pytest.param(
            "from typing import TYPE_CHECKING\n\nif TYPE_CHECKING:\n    import os\n",
            id="type-checking",
        ),
        pytest.param('if __name__ == "__main__":\n    raise SystemExit(used())\n', id="main-guard"),
        pytest.param(
            "from typing import overload\n\n\n@overload\ndef pick(x: int) -> int: ...\n"
            "@overload\ndef pick(x: str) -> str: ...\n"
            "def pick(x: int | str) -> int | str:\n    return x\n\n\n"
            "pick(1)\n",
            id="overload",
        ),
    ],
)
def test_coverage_exempts_code_that_never_runs_under_test(project: Project, block: str) -> None:
    importable(project)
    project.write("src/demo/calc.py", f"def used() -> int:\n    return 1\n\n\n{block}")
    project.write("tests/test_calc.py", TESTS_USED)
    assert project.make("coverage").returncode == 0


def test_coverage_counts_code_run_in_a_subprocess(project: Project) -> None:
    importable(project)
    project.write("src/demo/calc.py", "def used() -> int:\n    return 1\n")
    project.write("src/demo/tool.py", 'print("ran")  # noqa: T201\n')
    project.write(
        "tests/test_calc.py",
        TESTS_USED
        + "\n\ndef test_tool() -> None:\n"
        + "    import os, subprocess, sys\n"
        + '    env = {**os.environ, "PYTHONPATH": "src"}\n'
        + '    subprocess.run([sys.executable, "-m", "demo.tool"], env=env, check=True)\n',
    )
    assert project.make("coverage").returncode == 0


def test_coverage_leaves_no_data_file_in_the_repository(project: Project) -> None:
    importable(project)
    project.write("src/demo/calc.py", "def used() -> int:\n    return 1\n")
    project.write("tests/test_calc.py", TESTS_USED)
    assert project.make("coverage").returncode == 0
    assert sorted(path.name for path in project.root.glob(".coverage*")) == []


def test_coverage_refuses_a_unit_with_source_and_no_tests(project: Project) -> None:
    result = project.make("coverage")
    assert result.returncode != 0
    assert "coverage: . has src and no tests at tests" in result.stderr


def test_coverage_skips_a_unit_without_source(project: Project) -> None:
    (project.root / "src" / "demo" / "__init__.py").unlink()
    (project.root / "src" / "demo").rmdir()
    (project.root / "src").rmdir()
    result = project.make("coverage")
    assert result.returncode == 0
    assert "·  skip coverage . (no src)" in result.stdout


def test_coverage_refuses_to_run_beside_a_config_it_would_ignore(project: Project) -> None:
    project.write("pyproject.toml", manifest() + "\n[tool.coverage.report]\nfail_under = 50\n")
    result = project.make("coverage")
    assert result.returncode != 0
    assert "wiring: pyproject.toml: coverage runs on the harness's configuration" in result.stderr


# #region Declared layout


def test_test_runs_the_declared_suite(project: Project) -> None:
    declare_layout(project)
    project.write("src/test/test_unit.py", FAILING)
    assert "FAILED src/test/test_unit.py::test_fails" in project.make("test").stdout


def test_coverage_measures_the_declared_source(project: Project) -> None:
    declare_layout(project)
    project.write("src/main/demo/calc.py", "def used() -> int:\n    return 1\n")
    project.write("src/test/test_calc.py", TESTS_USED)
    result = project.make("coverage")
    assert result.returncode == 0
    assert "Required test coverage of 100.0% reached" in result.stdout
