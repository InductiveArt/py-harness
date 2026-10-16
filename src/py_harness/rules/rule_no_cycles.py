import sys
from collections.abc import Iterator
from collections.abc import Mapping
from collections.abc import Set
from dataclasses import dataclass
from dataclasses import field
from pathlib import Path

import grimp

from py_harness.console import out
from py_harness.layout import source_packages
from py_harness.layout import source_root
from py_harness.units import units_in_scope


def main() -> int:
    report = [line for unit in units_in_scope(Path.cwd()) for line in unit_report(unit)]
    if not report:
        out("doctor: no-cycles OK")
        return 0
    out("Import cycles (forbidden):")
    for line in report:
        out(f"  {line}")
    out()
    out("Rule: the import graph is acyclic. Imports under `if TYPE_CHECKING:` are exempt,")
    out("as they never run.")
    out("Move what both sides need into a module each of them imports, or invert one of the edges.")
    return 1


def unit_report(unit: Path) -> list[str]:
    packages = source_packages(unit)
    if not packages:
        return []
    # The unit's own source goes first on the path, so the graph is of this
    # tree and never of an installed copy.
    sys.path.insert(0, str(source_root(unit).resolve()))
    graph = grimp.build_graph(*packages, exclude_type_checking_imports=True, cache_dir=None)
    edges = {module: graph.find_modules_directly_imported_by(module) for module in graph.modules}
    lines: list[str] = []
    for component in ComponentSearch(edges).cycles():
        lines.append(f"cycle in {unit.as_posix()}:")
        for importer in component:
            for imported in sorted(edges[importer] & set(component)):
                details = graph.get_import_details(importer=importer, imported=imported)
                numbers = ", ".join(
                    str(number) for number in sorted(int(d["line_number"]) for d in details)
                )
                lines.append(f"  {importer} -> {imported} (line {numbers})")
    return lines


@dataclass
class ComponentSearch:
    """Strongly connected components of an import graph, by an iterative Tarjan search."""

    edges: Mapping[str, Set[str]]
    index: dict[str, int] = field(default_factory=dict[str, int])
    low: dict[str, int] = field(default_factory=dict[str, int])
    stack: list[str] = field(default_factory=list[str])
    on_stack: set[str] = field(default_factory=set[str])
    components: list[list[str]] = field(default_factory=list[list[str]])

    def cycles(self) -> list[list[str]]:
        for module in sorted(self.edges):
            if module not in self.index:
                self.visit(module)
        return sorted(sorted(component) for component in self.components if len(component) > 1)

    def visit(self, start: str) -> None:
        work = [(start, self.enter(start))]
        while work:
            module, pending = work[-1]
            following = next(pending, None)
            if following is None:
                work.pop()
                self.leave(module, work[-1][0] if work else None)
            elif following not in self.index:
                work.append((following, self.enter(following)))
            elif following in self.on_stack:
                self.low[module] = min(self.low[module], self.index[following])

    def enter(self, module: str) -> Iterator[str]:
        self.index[module] = self.low[module] = len(self.index)
        self.stack.append(module)
        self.on_stack.add(module)
        return iter(sorted(self.edges.get(module, set[str]())))

    def leave(self, module: str, parent: str | None) -> None:
        if self.low[module] == self.index[module]:
            component: list[str] = []
            while module not in component:
                member = self.stack.pop()
                self.on_stack.discard(member)
                component.append(member)
            self.components.append(component)
        if parent is not None:
            self.low[parent] = min(self.low[parent], self.low[module])


if __name__ == "__main__":
    raise SystemExit(main())
