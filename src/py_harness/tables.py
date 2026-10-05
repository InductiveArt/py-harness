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


def integer(table: Table, key: str) -> int | None:
    value = table.get(key)
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def as_table(value: object) -> Table:
    """A JSON object read as a table; any other value as an empty one."""
    return cast("Table", value) if isinstance(value, dict) else {}


def as_list(value: object) -> list[object]:
    """A JSON array read as a list; any other value as an empty one."""
    return items_of(value) or []


def items_of(value: object) -> list[object] | None:
    """The items of a JSON array, or None when the value is not one."""
    return cast("list[object]", value) if isinstance(value, list) else None
