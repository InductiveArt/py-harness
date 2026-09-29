import ast
from collections.abc import Iterator
from pathlib import Path

from py_harness.console import out
from py_harness.layout import source_root
from py_harness.units import units_in_scope


def main() -> int:
    units = units_in_scope(Path.cwd())
    violations = [
        violation
        for unit in units
        for path in sorted(source_root(unit).rglob("*.py"))
        for violation in reexports(path)
    ]
    if not violations:
        out("doctor: no-reexport OK")
        return 0
    out("Re-exports (forbidden):")
    for violation in violations:
        out(f"  {violation}")
    out()
    out("Rule: a symbol is imported from the module that defines it, and from nowhere else.")
    out("Import it from there and delete the re-export.")
    return 1


def reexports(path: Path) -> Iterator[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    imported = imported_names(tree)
    for node in ast.walk(tree):
        for finding in [*redundant_aliases(node), *exported_imports(node, imported)]:
            yield f"{path.as_posix()}:{getattr(node, 'lineno', 0)}  {finding}"
    for statement in tree.body:
        for finding in aliased_imports(statement, imported):
            yield f"{path.as_posix()}:{statement.lineno}  {finding}"


def imported_names(tree: ast.Module) -> set[str]:
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(alias.asname or alias.name.partition(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            names.update(alias.asname or alias.name for alias in node.names if alias.name != "*")
    return names


def redundant_aliases(node: ast.AST) -> list[str]:
    """An alias identical to the imported name is the spelling that declares a re-export."""
    if not isinstance(node, ast.Import | ast.ImportFrom):
        return []
    return [
        f"`{alias.name} as {alias.asname}`" for alias in node.names if alias.asname == alias.name
    ]


def aliased_imports(statement: ast.stmt, imported: set[str]) -> list[str]:
    """A public module-level name bound to an import is one more door to it."""
    match statement:
        case ast.Assign(targets=targets, value=value):
            names = [target.id for target in targets if isinstance(target, ast.Name)]
        case ast.AnnAssign(target=ast.Name(id=name), value=ast.expr() as value):
            names = [name]
        case _:
            return []
    if not is_import_path(value, imported):
        return []
    return [
        f"`{name} = {ast.unparse(value)}` makes an imported name importable from here"
        for name in names
        if not name.startswith("_")
    ]


def is_import_path(value: ast.expr, imported: set[str]) -> bool:
    """An imported name, or an attribute path starting from one."""
    while isinstance(value, ast.Attribute):
        value = value.value
    return isinstance(value, ast.Name) and value.id in imported


def exported_imports(node: ast.AST, imported: set[str]) -> list[str]:
    findings: list[str] = []
    for value in all_additions(node):
        names = literal_strings(value)
        if names is None:
            findings.append("`__all__` is built from something other than string literals")
        else:
            findings.extend(
                f"`__all__` lists `{name}`, which this module imports"
                for name in names
                if name in imported
            )
    return findings


def all_additions(node: ast.AST) -> list[ast.expr]:
    """The expressions a statement adds to `__all__`."""
    match node:
        case ast.Assign(targets=targets, value=value) if any(is_all(target) for target in targets):
            return [value]
        case ast.AnnAssign(target=target, value=ast.expr() as value) if is_all(target):
            return [value]
        case ast.AugAssign(target=target, value=value) if is_all(target):
            return [value]
        case ast.Call(
            func=ast.Attribute(value=owner, attr="append" | "extend"), args=arguments
        ) if is_all(owner):
            return arguments
        case _:
            return []


def is_all(node: ast.expr) -> bool:
    return isinstance(node, ast.Name) and node.id == "__all__"


def literal_strings(value: ast.expr) -> list[str] | None:
    elements = value.elts if isinstance(value, ast.List | ast.Tuple) else [value]
    strings = [
        element.value
        for element in elements
        if isinstance(element, ast.Constant) and isinstance(element.value, str)
    ]
    return strings if len(strings) == len(elements) else None


if __name__ == "__main__":
    raise SystemExit(main())
