import os
import subprocess
from pathlib import Path

import pytest

from tests.support import REPOSITORY
from tests.support import Project

CONSUMER_MANIFEST = """
[project]
name = "consumer"
version = "0.0.0"
requires-python = ">=3.11"

[dependency-groups]
dev = ["py-harness"]

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[tool.uv.sources]
py-harness = {{ path = "{wheel}" }}

[tool.ruff]
extend = ".venv/share/py-harness/ruff.toml"

[tool.basedpyright]
extends = ".venv/share/py-harness/pyrightconfig.json"
"""
TEST_CALC = """
from consumer.calc import double


def test_double() -> None:
    assert double(2) == 4
"""
pytestmark = pytest.mark.integration

SHARED = (
    "harness.mk",
    "ruff.toml",
    "pyrightconfig.json",
    "coverage.toml",
    "agent-rules.md",
    "hooks/vscode.json",
    "skills/commenting/SKILL.md",
    "skills/quality-tooling/SKILL.md",
)


def consumer_environment() -> dict[str, str]:
    """A consumer's own environment: its venv, nothing from this run, and only cached packages."""
    environment = dict(os.environ)
    for inherited in (
        "UV_PROJECT_ENVIRONMENT",
        "VIRTUAL_ENV",
        "PY_HARNESS_UNITS",
        "MAKEFLAGS",
        "MFLAGS",
        "MAKELEVEL",
    ):
        environment.pop(inherited, None)
    environment["UV_OFFLINE"] = "1"
    return environment


def run(command: list[str], root: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        command, cwd=root, env=consumer_environment(), capture_output=True, text=True, check=False
    )


@pytest.fixture(scope="module")
def consumer(tmp_path_factory: pytest.TempPathFactory) -> Project:
    """A project that installed the built wheel exactly as a consuming repository does."""
    base = tmp_path_factory.mktemp("consumable")
    built = run(["uv", "build", "--wheel", "--out-dir", str(base / "dist"), str(REPOSITORY)], base)
    assert built.returncode == 0, built.stderr
    wheel = next((base / "dist").glob("py_harness-*.whl"))
    created = Project(base / "consumer")
    created.write("pyproject.toml", CONSUMER_MANIFEST.format(wheel=wheel))
    created.write("Makefile", "include .venv/share/py-harness/harness.mk\n")
    created.write(".gitignore", ".venv/\n__pycache__/\n")
    created.write("src/consumer/__init__.py")
    created.write("src/consumer/calc.py", "def double(x: int) -> int:\n    return x * 2\n")
    created.write("tests/test_calc.py", TEST_CALC)
    links = created.root / ".claude" / "skills"
    links.mkdir(parents=True)
    for skill in ("commenting", "quality-tooling"):
        (links / skill).symlink_to(Path("../../.venv/share/py-harness/skills") / skill)
    created.write("CLAUDE.md", "@.venv/share/py-harness/agent-rules.md\n")
    instructions = created.root / ".github" / "instructions"
    instructions.mkdir(parents=True)
    (instructions / "py-harness.instructions.md").symlink_to(
        Path("../../.venv/share/py-harness/agent-rules.md")
    )
    created.git("init", "-q")
    for bootstrap in (["uv", "lock"], ["uv", "sync"]):
        completed = run(bootstrap, created.root)
        assert completed.returncode == 0, completed.stderr
    return created


def test_the_wheel_installs_its_configs_at_a_version_free_path(consumer: Project) -> None:
    shared = consumer.root / ".venv" / "share" / "py-harness"
    assert [name for name in SHARED if not (shared / name).is_file()] == []


def test_a_consumer_passes_ready(consumer: Project) -> None:
    result = run(["make", "-s", "ready"], consumer.root)
    assert result.returncode == 0, result.stdout[-3000:]
    assert "STATUS: PASSED" in result.stdout


def test_a_consumer_passes_ci(consumer: Project) -> None:
    result = run(["make", "-s", "ci"], consumer.root)
    assert result.returncode == 0, result.stdout[-3000:]
    assert "STATUS: PASSED" in result.stdout


def test_ci_fails_on_unformatted_code_without_rewriting_it(consumer: Project) -> None:
    unformatted = consumer.write("src/consumer/shape.py", "x=1\n")
    result = run(["make", "-s", "ci"], consumer.root)
    written = unformatted.read_text()
    unformatted.unlink()
    assert "STATUS: FAILED at stages `format`, " in result.stdout
    assert written == "x=1\n"


def test_update_relocks_and_syncs(consumer: Project) -> None:
    result = run(["make", "-s", "update"], consumer.root)
    assert result.returncode == 0, result.stderr
