---
name: py-quality-tooling
description: Use after changing code in a repository checked by py-harness and before saying the work is done; before running tests, lint, type checks, doctor or the check and ci loops; and when a check fails, to find the fix it expects.
---

# Quality tooling

`make` is the entry point for everything. The targets come from py-harness through the include in the repository's Makefile. `make help` is the authority on what exists; this skill says how to use it.

## The loops

| Loop | Command | Use |
|---|---|---|
| Smoke | `make test-pkg PKG=<name>` | One unit's tests while iterating. |
| Inner | `make check` | Did I break what I just touched. |
| Pre-commit | `make ready` | Is this ready to commit. |
| CI server | `make ci` | Does the code as committed pass. |
| Health | `make doctor` | Structural rules only; `RULE=<name>` runs one. |

The three composed loops run these stages in this order, and keep going after a stage fails, so one run names every failing stage:

- `check`: agent, wiring, lint-fix, format-fix, format, lint, typecheck, doctor, test.
- `ready`: install, agent, wiring, lint-fix, format-fix, format, lint, typecheck, doctor, coverage.
- `ci`: install, wiring, format, lint, typecheck, doctor, coverage. It never rewrites a file, so what a fix would repair fails instead; it is what a CI server runs, after `uv sync --locked`.

`agent` links the harness's rules and these skills into `.claude/`, pointing into the harness version the repository pins, and lists the links in `.git/info/exclude`, so no commit carries them.

Each prints one line per stage as it ends, then a block for each stage that did not pass, and its verdict on the last line. A test stage's line says how many tests passed and failed and, when it measures coverage, how much of which source the tests run. A stage that failed found something in the code: its block counts the findings per rule, shows the first few, and names the file holding all of them, one section per source file or test under a `## <path>` heading. Search those headings and read only the section you need. A stage that broke did not finish: its checker crashed, its report could not be read, or it ran past its time limit. Its result is unknown, so it is no finding to fix unless its last lines point at a file you changed; otherwise tell the user. Each stage's whole output is in its log, beside the findings files in `.git/py-harness/logs/<loop>/`. When a run was cut short, `make last` prints the newest run again from those files without running anything. The run also names the files it changed, or, when there are more than five, the file listing them, and the suppressions: how many the repository holds, how many the change since the last commit added or removed, the rules bypassed most, and each one added.

## Fix tiers

- `format-fix` rewrites layout only.
- `lint-fix` applies the fixes ruff classifies as safe: unused imports, import order, one symbol per import line, relative imports rewritten as absolute. It passes even when findings remain; `lint` reports those, under its own name.
- `lint-fix-unsafe` applies fixes that can change behaviour. No composed loop runs it. Run it deliberately and read the diff before keeping it.

## When a check fails

Each finding has one expected fix. A suppression is not one of them: it is the user's decision.

| Finding | Expected fix |
|---|---|
| Unformatted file, import order, several symbols on one import line, relative import | `make check` fixes it; rerun it. |
| `BLE001`, `S110`, `S112`: broad or silenced exception | Catch the exceptions the call can raise, and handle or re-raise each. |
| `C901`: too complex | Extract named functions; a dispatch on a value becomes a table or a `match`. |
| `E501`: long line | Split a message between sentences, never inside one, so each part stays searchable; or name a constant. The formatter wraps code. |
| `T201`: `print` | A logger for diagnostics; a program's own output goes through one module that owns it. |
| `FIX`: unfinished-work marker | Finish the work, or record it outside the code. |
| `ERA001`: commented-out code | Delete it; version control keeps it. |
| `TID251`: banned API | Follow its message: a skipped test, or one that ends itself early, passes or goes with its behaviour; an unchecked function gets its types fixed. |
| `reportAny`, `reportExplicitAny` | Parse the value into a declared type where it enters. |
| `reportUnknown*`, `reportMissingTypeStubs`: untyped library | Wrap the library in one typed module; everything else imports that module. |
| `reportPrivateLocalImportUsage` | Import the name from the module that defines it. |
| `no-reexport` | Import from the defining module and delete the re-export. |
| `no-hidden-names` | Import the symbol under its own name (conventional aliases such as `np` and `pd` stay); when two names collide, import the module and qualify, or rename one where it is defined. Reach attributes and modules by name, never through a string. |
| `no-cycles` | Move what both sides need into a module each imports; a factory that imports its own implementations moves out of their base module. |
| `boundaries` | Invert the dependency: the lower layer defines the interface, the higher one implements it. |
| `no-comment-overreach` | State this file's own contract, or delete the comment. |
| `no-blanket-exemptions` | Remove the exemption; fix the findings it hid. |
| `no-secrets` | Read the value from the environment at runtime. |
| Coverage below 100% | Test the behaviour. Code no test can reach is dead, or needs a seam a test can drive. |

## Units and layout

`make units` lists what every stage covers: the members of a uv workspace in declaration order, then the root when the root is a project. A single project is one unit, `.`. `PKG=` takes a unit's project name, in any mix of case, `-` and `_`.

A unit's code lives in `src/` and its tests in `tests/`, unless the unit declares otherwise, once, in its own `pyproject.toml`:

```toml
[tool.py-harness]
source = "src/main"

[tool.pytest.ini_options]
testpaths = ["src/test"]
```

Every stage and rule reads those two declarations: coverage measures the source, the doctor rules scan it, and the tests are what the unit's pytest configuration names. A test marked `integration` (`pytestmark = pytest.mark.integration` in its module, or the decorator) runs only in `test-integration`; `test` runs every other test, and `coverage` runs them all. Each test file is imported by its path: two files in different folders may share a name, and no test file imports another by its bare name, so shared test code goes in `conftest.py` fixtures.

A new workspace member needs nothing beyond its entry in `[tool.uv.workspace] members`; `make units` shows it at once.

## Coverage

`ready` and `ci` require full branch coverage of each unit's source by that unit's own tests. A unit with source and no tests fails.

- `# pragma: no cover` excludes nothing.
- Exempt, because it never runs under test: `if TYPE_CHECKING:` blocks, `if __name__ == "__main__":` guards and `@overload` stubs.
- Code a test runs in a subprocess counts.

## Types

basedpyright checks the whole repository in strict mode, and beyond it:

- Every type ignore names its rule, `# pyright: ignore[reportX]`, and fails when it suppresses nothing.
- Importing a name from a module that merely imported it fails.
- Writing `Any` fails, and so does any expression whose type is `Any`. Where a library hands back `Any` (`json.loads`, `re.findall`, an untyped configuration), convert the value where it enters, or iterate `re` matches rather than calling `findall`.

Annotate every signature, and every value whose type inference cannot know (an empty container, a `None` filled in later). Never annotate a value that only restates what its right-hand side already declares: the type would then live in two places.

## Doctor

Shared checks: `no-cycles`, `no-reexport`, `no-hidden-names`, `no-comment-overreach`, `boundaries`, `no-secrets`, `no-blanket-exemptions`, and two reports, which never fail. `suppressions` lists every bypass in the repository by rule and by file, with its density per 1,000 lines of Python. `shared-names` lists each public name defined in more than one module; judge each one: a copy to merge, two meanings to name apart, or a protocol shared on purpose. Imports under `if TYPE_CHECKING:` are the only ones `no-cycles` ignores; an import inside a function still closes a cycle.

Layer contracts go in `[tool.importlinter]` in the root `pyproject.toml`; `boundaries` enforces them, and says so when none are declared.

A repository's own checks go in `.py-harness/doctor/`, named by kind: `rule-*.py` fails on a violation, `drift-*.py` fails on diverged configuration, `report-*.py` only informs. Each is a script that exits non-zero to fail; the resolved units arrive in `PY_HARNESS_UNITS`, one path per line, and the harness's own directory in `PY_HARNESS_DIR`. A failing check prints a heading ending in a colon, such as `Import cycles (forbidden):`, then one indented line per finding, with any detail of a finding indented further; the summary gives each check its own line under the doctor's, with how many findings a failing one listed, and shows the heading and the first finding. A passing check prints one line, `doctor: <name> OK`, or what else it has to say, such as why it skipped. A check that raises instead of exiting is reported as broken, not as a finding.

## Suppressions

Every suppression names what it suppresses, covers one line, and must suppress something:

| Form | Where | Unused or bare |
|---|---|---|
| `# noqa: CODE` | lint | fails |
| `# pyright: ignore[rule]` | types | fails |
| `# pragma: allowlist secret` | `no-secrets` | fails |
| `@pytest.mark.xfail` | tests | a passing test fails; `strict=False` or `run=False` is refused |
| `cast(T, value)` | types | an unnecessary one fails |

Anything wider is refused. `no-blanket-exemptions` rejects a file-level `# pyright:` directive, a `# type: ignore` on a line of its own, `# ruff: noqa` or `# flake8: noqa`, `# fmt: off` or `# fmt: skip`, and `# isort: skip_file`, `skip` or `off`. Skipping a test through pytest or unittest, ending one early with `pytest.xfail()`, and `@no_type_check` are lint findings. A repository's own ruff and basedpyright settings may add rules and describe the environment, never remove or lower one: `wiring` refuses an `ignore`, a per-file ignore, a replaced `select`, a raised threshold, a type rule or mode below the shared one, a pytest setting that changes which tests run or how they count (`addopts`, the collection patterns, `xfail_strict`), and pytest settings anywhere but `pyproject.toml`. The test stages drop `PYTEST_ADDOPTS` from the environment they run in.

There is no baseline file, no skip list and no way to exclude a finding from outside the code. `.py-harness/ignore` removes a whole unit from every stage and announces it on every run; it is for units outside the bar, never for silencing a finding inside one.

## Change procedure

1. Make the change.
2. `make check`. Fix what the summary names, then run it again.
3. `make ready` before committing.
4. When a change touches many files across units, check the unit boundaries before going further.
