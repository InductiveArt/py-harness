---
name: py-quality-tooling
description: Use after changing code in a repository checked by py-harness and before saying the work is done; when running its make targets; and when a check fails, to find the fix it expects.
---

# Quality tooling

## Commands

| Command | When |
|---|---|
| `make check` | After every change. Fixes layout and safe lint findings, then runs lint, types, the doctor and the unit tests. |
| `make ready` | Before committing. The same, with every test, integration ones included, at full branch coverage. |
| `make last` | The latest run's summary again, running nothing. |
| `make lint`, `typecheck`, `test`, `coverage`, `doctor` | One stage alone. `make doctor RULE=<name>` runs one rule. |
| `make lint-fix-unsafe` | Fixes that can change behaviour. Run it deliberately and read the diff. |

`make ci` belongs to the CI server: it never fixes code. `make baseline` belongs to the user: never run it. `make help` lists every target.

## Reading a run

- A run prints one line per stage, a block for each stage that did not pass, and its verdict last.
- A failed stage found something in the code. Its block shows the first findings and names the file holding all of them, one `## <path>` section per source file or test: search the headings and read only the section you need.
- A `BROKEN` stage did not finish: its checker crashed, its report could not be read, or it ran out of time. It is no finding. Fix it only when its last lines point at a file you changed; otherwise tell the user.
- "Files changed during the run" lists the files the fixes rewrote. Re-read them before editing them again.
- A test that fails because a service it needs is not running, such as a database or a container, is no code finding. Tell the user; never mock it away or skip the test.

## Expected fixes

Each finding has one expected fix. A suppression is never one of them.

| Finding | Fix |
|---|---|
| `BLE001`, `S110`, `S112`: broad or silenced exception | Catch the exceptions the call can raise, and handle or re-raise each. |
| `C901`: too complex | Extract named functions; a dispatch on a value becomes a table or a `match`. |
| `E501`: long line | Split a message between sentences, so each part stays searchable, or name a constant. |
| `T201`: `print` | A logger for diagnostics; a program's own output goes through the one module that owns it. |
| `FIX`: `TODO` and its kin | Finish the work, or record it outside the code. |
| `ERA001`: commented-out code | Delete it. |
| `TID251`: banned API | Follow its message. |
| `reportAny`, `reportExplicitAny` | Turn the value into a declared type where it enters: a model, a `TypedDict`, an `isinstance` check. |
| `reportUnknown*`, `reportMissingTypeArgument`, `reportMissingParameterType` | Annotate the source of the unknown, usually a parameter or a bare `dict` or `list` upstream, not each use. |
| `reportMissingTypeStubs`: untyped library | Its stub package (`types-*` or `*-stubs`) as a dev dependency; else a stub in `typings/<library>/__init__.pyi` declaring only what the code calls; a wrapper module only as a last resort. |
| `reportPrivateLocalImportUsage`, `no-reexport` | Import the name from the module that defines it. |
| `no-hidden-names` | Import the symbol under its own name (`np`, `pd` and the like stay); reach attributes and modules by name, never through a string. |
| `no-cycles` | Move what both sides need into a module each imports. |
| `boundaries` | Invert the dependency: the lower layer defines the interface, the higher one implements it. |
| `no-comment-overreach` | State this file's own contract, or delete the comment. |
| `no-blanket-exemptions` | Remove the exemption and fix what it hid. |
| Coverage below 100% | Test the behaviour. Code no test can reach is dead, or needs a seam a test can drive. |

## Types

basedpyright runs in strict mode over the whole repository. Beyond strict, writing `Any` fails, and so does any expression whose type is `Any`; every type ignore names its rule and fails when it suppresses nothing.

Annotate every signature, and every value whose type inference cannot know, such as an empty container. Never annotate a value whose right-hand side already declares its type.

Dataframes: frames in, typed values out. Operations on a frame are typed by its library, or by `pandas-stubs`. A value leaving the frame is not:

- A row or record leaves as a model: `Model.model_validate(row)` for each row of `iter_rows(named=True)` or `to_dict("records")`.
- A single value is checked as it leaves: `isinstance`, raising when it fails, since a column's maximum is `None` on an empty frame.
- A column expression replaces a lambda in `apply`; when a function is needed, give it a typed parameter.

## Tests and coverage

- `test` runs every test not marked `integration` (`pytestmark = pytest.mark.integration`); `coverage` runs them all.
- `ready` requires full branch coverage of the source by its own tests. `# pragma: no cover` excludes nothing; only `if TYPE_CHECKING:`, `if __name__ == "__main__":` and `@overload` are exempt. Code a test runs in a subprocess counts.
- Each test file is imported by its path, so no test file imports another: shared test code goes in `conftest.py` fixtures.

## Suppressions

A suppression is the user's decision: ask before adding one, and say why. The forms that exist each cover one line, name what they suppress, and fail when they suppress nothing:

| Form | Where |
|---|---|
| `# noqa: CODE` | lint |
| `# pyright: ignore[rule]` | types |
| `cast(T, value)` | types |
| `@pytest.mark.xfail` | tests, strict only |

Anything wider fails: a file-level directive, a bare `noqa` or type ignore, `# fmt: off`, a skipped test. A repository's own tool settings may add rules, never lower one.

`.basedpyright/baseline.json` holds the type errors the codebase had when it adopted the harness; `typecheck` fails only on new ones, and its line says how many are recorded. Fixing a recorded error drops it from the file on the next run: keep the shrunk file in the change.
