import tomllib
from pathlib import Path
from typing import TypeAlias
from typing import cast

Table: TypeAlias = dict[str, object]


def read_table(path: Path) -> Table:
    with path.open("rb") as file:
        return tomllib.load(file)


def subtable(table: Table, *keys: str) -> Table | None:
    current = table
    for key in keys:
        value = current.get(key)
        if not isinstance(value, dict):
            return None
        # A TOML table's keys are always strings.
        current = cast("Table", value)
    return current


def leaf_keys(table: Table, prefix: str = "") -> list[str]:
    """Every setting in a table, nested tables flattened into dotted keys."""
    keys: list[str] = []
    for key in table:
        nested = subtable(table, key)
        if nested is None:
            keys.append(f"{prefix}{key}")
        else:
            keys.extend(leaf_keys(nested, f"{prefix}{key}."))
    return keys


def string_list(table: Table | None, key: str) -> list[str]:
    value = None if table is None else table.get(key)
    if not isinstance(value, list):
        return []
    return [str(item) for item in cast("list[object]", value)]


def string(table: Table | None, key: str) -> str | None:
    value = None if table is None else table.get(key)
    return value if isinstance(value, str) else None
