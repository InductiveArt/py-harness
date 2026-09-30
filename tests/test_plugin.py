import json
from pathlib import Path
from typing import cast

from py_harness.tables import read_table
from py_harness.tables import string
from py_harness.tables import subtable
from tests.support import REPOSITORY
from tests.support import SHARE

PLUGIN = REPOSITORY / ".claude-plugin" / "plugin.json"
MARKETPLACE = REPOSITORY / ".claude-plugin" / "marketplace.json"


def manifest(path: Path) -> dict[str, object]:
    # Both manifests are JSON objects; a malformed one fails the test that reads it.
    return cast("dict[str, object]", json.loads(path.read_text(encoding="utf-8")))


def test_the_plugin_is_versioned_with_the_package() -> None:
    project = subtable(read_table(REPOSITORY / "pyproject.toml"), "project")
    assert manifest(PLUGIN)["version"] == string(project, "version")


def test_the_plugin_serves_the_shared_skills_from_their_one_copy() -> None:
    skills = REPOSITORY / str(manifest(PLUGIN)["skills"])
    assert skills.resolve() == (SHARE / "skills").resolve()
    assert sorted(path.name for path in skills.iterdir()) == ["commenting", "quality-tooling"]


def test_the_marketplace_lists_the_plugin_from_this_repository() -> None:
    plugin = manifest(PLUGIN)
    listed = {"name": plugin["name"], "source": "./", "description": plugin["description"]}
    assert manifest(MARKETPLACE)["plugins"] == [listed]
