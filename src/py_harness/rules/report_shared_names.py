import ast
from collections import defaultdict
from pathlib import Path

from py_harness.console import out
from py_harness.units import repository_python_files

# Names every module of their kind defines by convention, so sharing one says nothing.
CONVENTIONAL_NAMES = frozenset({"main", "pytestmark"})


def main() -> int:
    definitions: dict[str, list[str]] = defaultdict(list)
    for path in repository_python_files(Path.cwd()):
        for name, line in module_definitions(path):
            definitions[name].append(f"{path.as_posix()}:{line}")
    shared = {name: places for name, places in definitions.items() if len(places) > 1}
    if not shared:
        out("doctor: shared-names none")
        return 0
    out(f"Names defined in more than one module ({len(shared)}):")
    for name in sorted(shared):
        out(f"  {name}  {', '.join(shared[name])}")
    out("Each is a copy to merge, two meanings to name apart, or a protocol shared on purpose.")
    return 0


def module_definitions(path: Path) -> list[tuple[str, int]]:
    """Each public name the module's top level defines, at its first definition."""
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    first: dict[str, int] = {}
    for node in tree.body:
        for name in defined_names(node):
            if not name.startswith("_") and name not in CONVENTIONAL_NAMES:
                first.setdefault(name, node.lineno)
    return list(first.items())


def defined_names(node: ast.stmt) -> list[str]:
    match node:
        case ast.FunctionDef(name=name) | ast.AsyncFunctionDef(name=name) | ast.ClassDef(name=name):
            return [name]
        case ast.Assign(targets=targets):
            return [target.id for target in targets if isinstance(target, ast.Name)]
        case ast.AnnAssign(target=ast.Name(id=name)):
            return [name]
        case _:
            return []


if __name__ == "__main__":
    raise SystemExit(main())
