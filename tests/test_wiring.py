import shutil

import pytest

from py_harness.wiring import wiring_problems
from tests.support import SHARE
from tests.support import Project
from tests.support import manifest

BASEDPYRIGHT_TABLE = f'[tool.basedpyright]\nextends = "{SHARE / "pyrightconfig.json"}"\n'
RUFF_TABLE = f'[tool.ruff]\nextend = "{SHARE / "ruff.toml"}"\n'
PROJECT_TABLE = '[project]\nname = "demo"\nversion = "0.0.0"\n'


def problems(project: Project) -> list[str]:
    return wiring_problems(project.root, SHARE)


def test_the_wiring_target_passes_a_wired_project(project: Project) -> None:
    assert project.make("wiring").returncode == 0


def test_lint_refuses_to_run_on_an_unwired_project(project: Project) -> None:
    project.write("pyproject.toml", PROJECT_TABLE + BASEDPYRIGHT_TABLE)
    result = project.make("lint")
    assert result.returncode != 0
    assert "wiring: no ruff configuration at the project root" in result.stderr


def test_a_project_without_a_manifest_is_a_problem(project: Project) -> None:
    (project.root / "pyproject.toml").unlink()
    assert problems(project) == ["no pyproject.toml at the project root; no tool can be wired"]


# #region Ruff


def test_a_nested_ruff_config_must_reach_the_harness(project: Project) -> None:
    project.write("libs/a/ruff.toml", 'extend-select = ["D"]\n')
    assert problems(project) == [
        f"libs/a/ruff.toml: does not extend {(SHARE / 'ruff.toml').as_posix()}"
    ]


def test_a_nested_config_may_reach_the_harness_through_the_root(project: Project) -> None:
    project.write("libs/a/pyproject.toml", '[tool.ruff]\nextend = "../../pyproject.toml"\n')
    assert problems(project) == []


def test_an_extend_path_expands_environment_variables(
    project: Project, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("HARNESS_SHARE", str(SHARE))
    project.write(
        "pyproject.toml",
        PROJECT_TABLE + '[tool.ruff]\nextend = "${HARNESS_SHARE}/ruff.toml"\n' + BASEDPYRIGHT_TABLE,
    )
    assert problems(project) == []


def test_an_extend_to_a_missing_file_is_a_problem(project: Project) -> None:
    project.write(
        "pyproject.toml", PROJECT_TABLE + '[tool.ruff]\nextend = "gone.toml"\n' + BASEDPYRIGHT_TABLE
    )
    assert problems(project) == ["pyproject.toml: extends 'gone.toml', which does not exist"]


def test_an_extend_loop_is_a_problem(project: Project) -> None:
    project.write("a/ruff.toml", 'extend = "../b/ruff.toml"\n')
    project.write("b/ruff.toml", 'extend = "../a/ruff.toml"\n')
    assert "a/ruff.toml: extends '../b/ruff.toml', which leads back to itself" in problems(project)


# #region Basedpyright


def test_a_missing_basedpyright_table_is_a_problem(project: Project) -> None:
    project.write("pyproject.toml", PROJECT_TABLE + RUFF_TABLE)
    assert problems(project) == [
        "pyproject.toml: no [tool.basedpyright]; basedpyright would run on its defaults"
    ]


def test_basedpyright_must_extend_the_harness_config(project: Project) -> None:
    project.write(
        "pyproject.toml",
        PROJECT_TABLE + RUFF_TABLE + '[tool.basedpyright]\nextends = "gone.json"\n',
    )
    assert problems(project) == [
        "pyproject.toml: [tool.basedpyright] extends must name "
        + (SHARE / "pyrightconfig.json").as_posix()
    ]


def test_a_pyrightconfig_file_is_a_problem(project: Project) -> None:
    project.write("pyrightconfig.json", "{}\n")
    assert problems(project) == [
        "pyrightconfig.json: basedpyright is configured only in the root pyproject.toml"
    ]


def test_a_pyright_table_is_a_problem(project: Project) -> None:
    project.write("pyproject.toml", manifest() + "\n[tool.pyright]\nstrict = []\n")
    assert problems(project) == [
        "pyproject.toml: [tool.pyright] is not read; use the root [tool.basedpyright]"
    ]


def test_a_nested_basedpyright_table_is_a_problem(project: Project) -> None:
    project.write("libs/a/pyproject.toml", '[tool.basedpyright]\ntypeCheckingMode = "off"\n')
    assert problems(project) == [
        "libs/a/pyproject.toml: [tool.basedpyright] is read only from the root pyproject.toml"
    ]


# #region Coverage


def test_a_coverage_table_is_a_problem(project: Project) -> None:
    project.write("pyproject.toml", manifest() + "\n[tool.coverage.report]\nfail_under = 50\n")
    assert problems(project) == [
        "pyproject.toml: coverage runs on the harness's configuration, never this one"
    ]


def test_a_coveragerc_is_a_problem(project: Project) -> None:
    project.write(".coveragerc", "[report]\nfail_under = 50\n")
    assert problems(project) == [
        ".coveragerc: coverage runs on the harness's configuration, never this one"
    ]


# #region Pytest

PYTEST_REFUSED = "can change which tests run or how they count; the harness decides both"


@pytest.mark.parametrize(
    ("name", "text"),
    [
        pytest.param("pytest.ini", "[pytest]\n", id="pytest-ini"),
        pytest.param(".pytest.ini", "[pytest]\n", id="hidden-pytest-ini"),
        pytest.param("tox.ini", "[tox]\n\n[pytest]\naddopts = -x\n", id="tox-ini"),
        pytest.param("setup.cfg", "[tool:pytest]\naddopts = -x\n", id="setup-cfg"),
    ],
)
def test_a_pytest_config_outside_pyproject_is_a_problem(
    project: Project, name: str, text: str
) -> None:
    project.write(name, text)
    assert problems(project) == [
        f"{name}: pytest settings belong only in pyproject.toml, where they are audited"
    ]


def test_a_file_without_a_pytest_section_is_not_a_pytest_config(project: Project) -> None:
    project.write("tox.ini", "[tox]\nenvlist = py311\n")
    assert problems(project) == []


@pytest.mark.parametrize(
    ("table", "key"),
    [
        pytest.param("tool.pytest.ini_options", "addopts", id="ini-style-addopts"),
        pytest.param("tool.pytest.ini_options", "python_files", id="collection-pattern"),
        pytest.param("tool.pytest", "addopts", id="native-addopts"),
    ],
)
def test_a_pytest_setting_that_can_narrow_the_run_is_a_problem(
    project: Project, table: str, key: str
) -> None:
    project.write("pyproject.toml", manifest() + f'\n[{table}]\n{key} = ["x"]\n')
    assert problems(project) == [f"pyproject.toml: pytest `{key}` {PYTEST_REFUSED}"]


def test_a_pytest_setting_that_names_tests_and_markers_is_allowed(project: Project) -> None:
    settings = '\n[tool.pytest.ini_options]\ntestpaths = ["tests"]\nmarkers = ["slow: slow"]\n'
    project.write("pyproject.toml", manifest() + settings)
    assert problems(project) == []


# #region Search


def test_hidden_directories_are_not_searched(project: Project) -> None:
    project.write(".venv/lib/pyproject.toml", "[tool.ruff]\nline-length = 80\n")
    assert problems(project) == []


def test_the_harness_directory_is_not_searched(project: Project) -> None:
    vendored = project.root / "vendored"
    shutil.copytree(SHARE, vendored)
    project.write(
        "pyproject.toml",
        PROJECT_TABLE
        + '[tool.ruff]\nextend = "vendored/ruff.toml"\n'
        + '[tool.basedpyright]\nextends = "vendored/pyrightconfig.json"\n',
    )
    assert wiring_problems(project.root, vendored) == []


# #region Floor

RUFF_LOWERED = (
    "can remove or relax a shared rule; a repository's"
    " ruff settings only extend the harness, name sources, or add rules"
)
BASEDPYRIGHT_LOWERED = (
    "can lower the shared type checking;"
    ' only environment settings and rules raised to "error" belong here'
)


def with_settings(project: Project, ruff: str = "", basedpyright: str = "") -> None:
    project.write(
        "pyproject.toml", PROJECT_TABLE + RUFF_TABLE + ruff + BASEDPYRIGHT_TABLE + basedpyright
    )


@pytest.mark.parametrize(
    ("setting", "key"),
    [
        pytest.param(
            '[tool.ruff.lint]\nextend-ignore = ["T201"]\n', "lint.extend-ignore", id="extend-ignore"
        ),
        pytest.param('[tool.ruff.lint]\nignore = ["T201"]\n', "lint.ignore", id="ignore"),
        pytest.param('[tool.ruff.lint]\nselect = ["E"]\n', "lint.select", id="select"),
        pytest.param(
            '[tool.ruff.lint.per-file-ignores]\n"tests/*" = ["S105"]\n',
            "lint.per-file-ignores.tests/*",
            id="per-file-ignores",
        ),
        pytest.param(
            "[tool.ruff.lint.mccabe]\nmax-complexity = 30\n",
            "lint.mccabe.max-complexity",
            id="threshold",
        ),
        pytest.param('extend-exclude = ["generated"]\n', "extend-exclude", id="exclude"),
        pytest.param(
            "[tool.ruff.lint.isort]\nforce-single-line = false\n",
            "lint.isort.force-single-line",
            id="import-layout",
        ),
    ],
)
def test_a_ruff_setting_that_can_lower_the_floor_is_a_problem(
    project: Project, setting: str, key: str
) -> None:
    with_settings(project, ruff=setting)
    assert problems(project) == [f"pyproject.toml: `{key}` {RUFF_LOWERED}"]


@pytest.mark.parametrize(
    "setting",
    [
        pytest.param('src = ["src/main"]\n', id="src"),
        pytest.param('namespace-packages = ["src/ns"]\n', id="namespace-packages"),
        pytest.param('[tool.ruff.lint]\nextend-select = ["D"]\n', id="extend-select"),
        pytest.param('[tool.ruff.lint.isort]\nknown-first-party = ["demo"]\n', id="isort"),
    ],
)
def test_a_ruff_setting_that_only_adds_is_allowed(project: Project, setting: str) -> None:
    with_settings(project, ruff=setting)
    assert problems(project) == []


@pytest.mark.parametrize(
    ("setting", "key"),
    [
        pytest.param('reportAny = "none"\n', "reportAny", id="rule-off"),
        pytest.param(
            'reportMissingSuperCall = "warning"\n', "reportMissingSuperCall", id="rule-below-error"
        ),
        pytest.param('typeCheckingMode = "standard"\n', "typeCheckingMode", id="mode"),
        pytest.param('ignore = ["src"]\n', "ignore", id="ignore"),
    ],
)
def test_a_basedpyright_setting_that_can_lower_the_floor_is_a_problem(
    project: Project, setting: str, key: str
) -> None:
    with_settings(project, basedpyright=setting)
    assert problems(project) == [
        f"pyproject.toml: [tool.basedpyright] `{key}` {BASEDPYRIGHT_LOWERED}"
    ]


@pytest.mark.parametrize(
    "setting",
    [
        pytest.param('reportMissingSuperCall = "error"\n', id="rule-raised"),
        pytest.param('pythonVersion = "3.11"\n', id="environment"),
    ],
)
def test_a_basedpyright_setting_that_only_adds_is_allowed(project: Project, setting: str) -> None:
    with_settings(project, basedpyright=setting)
    assert problems(project) == []
