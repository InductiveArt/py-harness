import os
import re
import subprocess
import textwrap
from dataclasses import dataclass
from pathlib import Path

REPOSITORY = Path(__file__).resolve().parents[1]
SHARE = REPOSITORY / "share"
VENV = REPOSITORY / ".venv"
PHONY = re.compile(r"^\.PHONY:((?:[^\n]*\\\n)*[^\n]*)", re.MULTILINE)
PASSING = "def test_passes() -> None:\n    assert True\n"


def manifest(name: str = "demo") -> str:
    return textwrap.dedent(
        f"""
        [project]
        name = "{name}"
        version = "0.0.0"
        requires-python = ">=3.11"

        [tool.ruff]
        extend = "{SHARE / "ruff.toml"}"

        [tool.basedpyright]
        extends = "{SHARE / "pyrightconfig.json"}"
        """
    ).lstrip()


def harness_environment() -> dict[str, str]:
    """This repository's venv stands in for a consumer's, with the outer run's state removed."""
    environment = dict(os.environ)
    environment["UV_PROJECT_ENVIRONMENT"] = str(VENV)
    for inherited in ("VIRTUAL_ENV", "PY_HARNESS_UNITS", "MAKEFLAGS", "MFLAGS", "MAKELEVEL"):
        environment.pop(inherited, None)
    return environment


@dataclass(frozen=True)
class Project:
    root: Path

    def write(self, relative: str, content: str = "") -> Path:
        path = self.root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(textwrap.dedent(content).lstrip())
        return path

    def read(self, relative: str) -> str:
        return (self.root / relative).read_text()

    def make(
        self, *arguments: str, environment: dict[str, str] | None = None
    ) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["make", "-s", *arguments],  # noqa: S607
            cwd=self.root,
            env={**harness_environment(), **(environment or {})},
            capture_output=True,
            text=True,
            check=False,
        )

    def git(self, *arguments: str) -> None:
        subprocess.run(["git", *arguments], cwd=self.root, capture_output=True, check=True)  # noqa: S607

    def commit(self) -> None:
        self.git("add", "-A")
        self.git("-c", "user.name=test", "-c", "user.email=test@local", "commit", "-qm", "snapshot")


def declared_targets() -> list[str]:
    """Every target the shared makefile declares phony, which is every target it defines."""
    found = PHONY.search((SHARE / "harness.mk").read_text())
    if found is None:
        raise AssertionError("harness.mk declares no .PHONY targets")
    return found[1].replace("\\", " ").split()


DECLARED_LAYOUT = """
[tool.py-harness]
source = "src/main"

[tool.pytest.ini_options]
testpaths = ["src/test"]
pythonpath = ["src/main"]
"""


def declare_layout(project: Project) -> None:
    """Moves the project to code in `src/main` and tests in `src/test`, declared once."""
    project.write("pyproject.toml", manifest() + DECLARED_LAYOUT)
    (project.root / "src" / "demo" / "__init__.py").unlink()
    (project.root / "src" / "demo").rmdir()
    project.write("src/main/demo/__init__.py")
