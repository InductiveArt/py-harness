import ast
from collections.abc import Iterator
from pathlib import Path

from py_harness.console import out
from py_harness.units import repository_python_files

# The alias every codebase gives these modules, so a search for their uses already tries it.
CONVENTIONAL_ALIASES = {
    "matplotlib.pyplot": "plt",
    "numpy": "np",
    "numpy.typing": "npt",
    "pandas": "pd",
    "polars": "pl",
    "pyarrow": "pa",
    "seaborn": "sns",
    "tensorflow": "tf",
    "xml.etree.ElementTree": "ET",
}
# Each reaches an attribute through its second argument, which names it only when it is a literal.
ATTRIBUTE_ACCESS = frozenset({"getattr", "setattr", "delattr", "hasattr"})
NAMESPACE_LOOKUPS = frozenset({"globals", "locals"})
# A class or module defining one of these answers to names no statement defines.
FALLBACK_LOOKUPS = frozenset({"__getattr__", "__getattribute__"})


def main() -> int:
    violations = [
        violation
        for path in repository_python_files(Path.cwd())
        for violation in hidden_names(path)
    ]
    if not violations:
        out("doctor: no-hidden-names OK")
        return 0
    out("Hidden names (forbidden):")
    for violation in violations:
        out(f"  {violation}")
    out()
    out("Rule: code spells a symbol by its own name wherever it uses it, so a search for the")
    out("name finds every use. Import it under its own name; when two names collide, import")
    out("the module and qualify, or rename one where it is defined. Reach attributes and")
    out("modules by name in the code, never through a string.")
    return 1


def hidden_names(path: Path) -> Iterator[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    for node in ast.walk(tree):
        for finding in [*renames(node), *runtime_lookups(node)]:
            yield f"{path.as_posix()}:{getattr(node, 'lineno', 0)}  {finding}"


def renames(node: ast.AST) -> list[str]:
    if isinstance(node, ast.Import):
        return [
            f"`{alias.name} as {alias.asname}`"
            for alias in node.names
            if alias.asname is not None and not is_own_name(alias.name, alias.asname)
        ]
    if isinstance(node, ast.ImportFrom):
        return [
            f"`{alias.name} as {alias.asname}`"
            for alias in node.names
            if alias.asname not in {None, alias.name}
        ]
    return []


def is_own_name(module: str, alias: str) -> bool:
    """A submodule aliased to its own last name binds what importing it from its package binds."""
    return alias == module.rpartition(".")[2] or CONVENTIONAL_ALIASES.get(module) == alias


def runtime_lookups(node: ast.AST) -> list[str]:
    match node:
        case ast.Call(func=ast.Name(id=name), args=[_, name_argument, *_]) if (
            name in ATTRIBUTE_ACCESS and not is_string_literal(name_argument)
        ):
            return [f"`{name}` with a computed name"]
        case ast.Call(func=ast.Name(id=name)) if name in NAMESPACE_LOOKUPS:
            return [f"`{name}()`"]
        case ast.Name(id="__import__"):
            return ["`__import__`"]
        case ast.Attribute(value=ast.Name(id="importlib"), attr="import_module"):
            return ["`importlib.import_module`"]
        case ast.ImportFrom(module="importlib", names=names) if any(
            alias.name == "import_module" for alias in names
        ):
            return ["`importlib.import_module`"]
        case ast.FunctionDef(name=name) | ast.AsyncFunctionDef(name=name) if (
            name in FALLBACK_LOOKUPS
        ):
            return [f"`def {name}`"]
        case _:
            return []


def is_string_literal(node: ast.expr) -> bool:
    return isinstance(node, ast.Constant) and isinstance(node.value, str)


if __name__ == "__main__":
    raise SystemExit(main())
