import os
import sys
from collections.abc import Callable
from collections.abc import Iterator
from pathlib import Path

from py_harness.console import err
from py_harness.tables import leaf_keys
from py_harness.tables import read_table
from py_harness.tables import string
from py_harness.tables import subtable
from py_harness.tree import repository_files

CONFIG_NAMES = frozenset(
    {
        "pyproject.toml",
        "ruff.toml",
        ".ruff.toml",
        "pyrightconfig.json",
        ".coveragerc",
        "pytest.ini",
        ".pytest.ini",
        "tox.ini",
        "setup.cfg",
    }
)
ROOT_MANIFEST = Path("pyproject.toml")
# What a repository's own tool settings may do: point at the harness, describe
# its environment, or add checks. Every other setting can lower the shared floor.
RUFF_ADDITIONS = frozenset(
    {
        "extend",
        "src",
        "namespace-packages",
        "extend-select",
        "lint.extend-select",
        "lint.isort.known-first-party",
        "lint.isort.known-third-party",
        "lint.isort.known-local-folder",
        "lint.isort.extra-standard-library",
    }
)
BASEDPYRIGHT_ENVIRONMENT = frozenset(
    {"extends", "pythonVersion", "pythonPlatform", "extraPaths", "venv", "venvPath"}
)
# Settings that change which tests pytest finds, or how an outcome counts.
PYTEST_REFUSED = frozenset(
    {
        "addopts",
        "norecursedirs",
        "python_files",
        "python_classes",
        "python_functions",
        "xfail_strict",
        "empty_parameter_set_mark",
    }
)
PYTEST_FILES = frozenset({"pytest.ini", ".pytest.ini"})
PYTEST_ELSEWHERE = "pytest settings belong only in pyproject.toml, where they are audited"
# Files that configure pytest only when they hold this section.
PYTEST_SECTIONS = {"tox.ini": "[pytest]", "setup.cfg": "[tool:pytest]"}


def main(argv: list[str]) -> int:
    harness = Path(argv[1])
    problems = wiring_problems(Path.cwd(), harness)
    for problem in problems:
        err(f"wiring: {problem}")
    return 1 if problems else 0


def wiring_problems(root: Path, harness: Path) -> list[str]:
    if not (root / ROOT_MANIFEST).is_file():
        return ["no pyproject.toml at the project root; no tool can be wired"]
    files = config_files(root, harness)
    return [
        *ruff_problems(root, harness / "ruff.toml", files),
        *basedpyright_problems(root, harness / "pyrightconfig.json", files),
        *coverage_problems(root, files),
        *pytest_problems(root, files),
    ]


def config_files(root: Path, harness: Path) -> list[Path]:
    """Every tool config below the root, the harness's own aside."""
    return repository_files(root, lambda name: name in CONFIG_NAMES, harness)


# #region Ruff


def ruff_problems(root: Path, harness_config: Path, files: list[Path]) -> Iterator[str]:
    configs = [file for file in files if is_ruff_config(root / file)]
    if not any(config.parent == Path() for config in configs):
        yield "no ruff configuration at the project root; ruff would run on its defaults"
    for config in configs:
        problem = chain_problem(root / config, harness_config.resolve(), ruff_extend)
        if problem is not None:
            yield f"{config.as_posix()}: {problem}"
        yield from lowered_ruff_settings(root, config)


def lowered_ruff_settings(root: Path, config: Path) -> Iterator[str]:
    for key in ruff_settings(root / config):
        if key not in RUFF_ADDITIONS:
            yield (
                f"{config.as_posix()}: `{key}` can remove or relax a shared rule; a repository's"
                " ruff settings only extend the harness, name sources, or add rules"
            )


def ruff_settings(path: Path) -> list[str]:
    table = read_table(path)
    settings = subtable(table, "tool", "ruff") if path.name == "pyproject.toml" else table
    return [] if settings is None else leaf_keys(settings)


def is_ruff_config(path: Path) -> bool:
    if path.name == "pyproject.toml":
        return subtable(read_table(path), "tool", "ruff") is not None
    return path.name in {"ruff.toml", ".ruff.toml"}


def ruff_extend(path: Path) -> str | None:
    table = read_table(path)
    if path.name == "pyproject.toml":
        return string(subtable(table, "tool", "ruff"), "extend")
    return string(table, "extend")


def chain_problem(start: Path, target: Path, extend_of: Callable[[Path], str | None]) -> str | None:
    """Follows `extend` links, resolved the way the tool resolves them, until one is the target."""
    seen: set[Path] = set()
    current = start.resolve()
    while current != target:
        raw = extend_of(current)
        if raw is None:
            return f"does not extend {target.as_posix()}"
        following = (current.parent / os.path.expandvars(os.path.expanduser(raw))).resolve()
        if not following.is_file():
            return f"extends '{raw}', which does not exist"
        if following in seen:
            return f"extends '{raw}', which leads back to itself"
        seen.add(following)
        current = following
    return None


# #region Basedpyright


def basedpyright_problems(root: Path, harness_config: Path, files: list[Path]) -> Iterator[str]:
    for file in files:
        yield from unread_basedpyright_config(root, file)
    settings = subtable(read_table(root / ROOT_MANIFEST), "tool", "basedpyright")
    if settings is None:
        yield "pyproject.toml: no [tool.basedpyright]; basedpyright would run on its defaults"
        return
    raw = string(settings, "extends")
    extended = None if raw is None else (root / raw).resolve()
    # A missing base leaves basedpyright on its defaults, and it still exits 0.
    if extended != harness_config.resolve():
        yield f"pyproject.toml: [tool.basedpyright] extends must name {harness_config.as_posix()}"
    for key, value in settings.items():
        if key not in BASEDPYRIGHT_ENVIRONMENT and not is_raised_rule(key, value):
            yield (
                f"pyproject.toml: [tool.basedpyright] `{key}` can lower the shared type checking;"
                ' only environment settings and rules raised to "error" belong here'
            )


def is_raised_rule(key: str, value: object) -> bool:
    return key.startswith("report") and value == "error"


def unread_basedpyright_config(root: Path, file: Path) -> Iterator[str]:
    if file.name == "pyrightconfig.json":
        yield f"{file.as_posix()}: basedpyright is configured only in the root pyproject.toml"
        return
    if file.name != "pyproject.toml":
        return
    tool = subtable(read_table(root / file), "tool")
    if tool is not None and "pyright" in tool:
        yield f"{file.as_posix()}: [tool.pyright] is not read; use the root [tool.basedpyright]"
    if tool is not None and "basedpyright" in tool and file != ROOT_MANIFEST:
        yield f"{file.as_posix()}: [tool.basedpyright] is read only from the root pyproject.toml"


# #region Coverage


def coverage_problems(root: Path, files: list[Path]) -> Iterator[str]:
    for file in files:
        if file.name == ".coveragerc" or has_coverage_table(root / file):
            yield f"{file.as_posix()}: coverage runs on the harness's configuration, never this one"


def has_coverage_table(path: Path) -> bool:
    return (
        path.name == "pyproject.toml" and subtable(read_table(path), "tool", "coverage") is not None
    )


# #region Pytest


def pytest_problems(root: Path, files: list[Path]) -> Iterator[str]:
    for file in files:
        if is_pytest_file(root / file):
            yield f"{file.as_posix()}: {PYTEST_ELSEWHERE}"
        for key in pytest_settings(root / file):
            if key in PYTEST_REFUSED:
                yield (
                    f"{file.as_posix()}: pytest `{key}` can change which tests run or how they"
                    " count; the harness decides both"
                )


def is_pytest_file(path: Path) -> bool:
    if path.name in PYTEST_FILES:
        return True
    section = PYTEST_SECTIONS.get(path.name)
    lines = path.read_text(encoding="utf-8").splitlines() if section is not None else []
    return any(line.strip() == section for line in lines)


def pytest_settings(path: Path) -> list[str]:
    """Every key of pyproject.toml's pytest table, the ini-style subtable's included."""
    settings = (
        subtable(read_table(path), "tool", "pytest") if path.name == ROOT_MANIFEST.name else None
    )
    if settings is None:
        return []
    keys = [key for key in settings if key != "ini_options"]
    ini_style = subtable(settings, "ini_options")
    return keys if ini_style is None else [*keys, *ini_style]


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
