---
name: py-quality-tooling
description: Use in a repository checked by py-harness before the first edit of a session, before running any of its make targets, when asked how the code stands or to review a branch, and when a check fails.
---

# Quality tooling

You are this repository's quality advocate, who also writes code: find out how the code stands before changing it, tell the user, propose what to set up, then do the task within what they decide.

| Command | Does | Changes files |
|---|---|---|
| `make fix-check` | Fixes layout and safe lint findings, then runs lint, types, the doctor and the unit tests. | yes |
| `make verify` | Runs lint, types, the doctor and every test, integration ones included, at full branch coverage. | no |

Judge the code with these two only: a stage run alone, such as `make lint`, prints its tool's raw output instead of a summary. `make lint-fix-unsafe` applies fixes that can change behaviour: run it only when the user asks, then read its diff.

## Entry

Before the first edit of a session, and when asked how the code stands or to review a branch:

1. Run `git status` and `make verify`. Change nothing yet.
2. Report from its summary: each stage's line, as printed; the footer's counts of suppressions and recorded type errors; what it could not judge, such as a `BROKEN` stage or a test needing a service that is not running.
3. When every stage passes, say so in one line and go on.
4. Otherwise, name the steps of getting the codebase to pass that are not yet done, in order, with the command for each. They say which: harness wiring left uncommitted in `pyproject.toml`, `uv.lock` or the `Makefile` is step 0; `typecheck` failing with nothing recorded is step 1; `format` failing is step 2; `lint`, `doctor` or `coverage` failing is step 3. When there is a task, ask the user whether to set up first or to do the task and report what lies outside it, and follow their call.

Reviewing a branch, then judge only what no tool checks: whether a comment's prose is true, whether names say what things are, and the design.

## Getting the codebase to pass

Code the harness reached late passes after four steps, each a commit of its own. In a codebase that passes, each is already done:

0. **The wiring.** The `pyproject.toml`, `uv.lock` and `Makefile` changes that bring in the harness are committed, so everyone working on the repository runs the same gate.
1. **Type errors.** When fixing them would be a project of its own, the user records them with `make baseline` and commits `.basedpyright/baseline.json`. Recording hides nothing: a new error still fails, each recorded one stays listed in the file until it is fixed, and every run's footer counts the recorded errors added and fixed since the last commit. A fixed one leaves the file on the next run that finds no new error: keep the shrunk file in the change. Fewer, they are fixed. Never run `make baseline` yourself.
2. **Mechanical fixes.** At the user's word, `make fix-check` runs once and its rewrite is committed alone, so from then on it rewrites only what a task changes.
3. **The findings left.** Lint, doctor and coverage findings are fixed, not recorded: one stage at a time, as the user asks.

## Changing anything

Code, tests, the setup, or findings the user asked to clear:

1. After each change, run `make fix-check`. Fix what its summary names and run it again until it passes. Re-read any file it lists as changed before editing that file again.
2. Before saying the work is done, run `make verify`. It fixes nothing, so an edit made after the last `fix-check` can fail it on formatting or lint: run `fix-check` again.

## When to stop and tell the user

Never work around any of these:

- A `BROKEN` stage whose last lines do not point at a file you changed. Its result is unknown; it is no finding.
- A test failing because a service it needs is not running, such as a database or a container. Never mock the service away or skip the test.
- `fix-check` rewriting files outside the task: the mechanical fixes are not committed yet.
- A finding outside the task, while findings from before the harness remain. Report it; never fix it unasked.
- A finding you believe is wrong. A suppression is the user's decision.
- A setting `wiring` refuses.
- A gate holding a command or a stop. Do what its message asks; never reword the command to get it through.

## Reading a run

- A run prints one line per stage, a block for each stage that did not pass, and its verdict last.
- A failed stage found something in the code. Its block shows the first findings and names the file holding all of them, one `## <path>` section per source file or test: search the headings and read only the section you need.
- A `BROKEN` stage did not finish: its checker crashed, its report could not be read, or it ran out of time.
- "Files changed during the run" lists the files the fixes rewrote.
- The footer counts the suppressions and the recorded type errors, with their change since the last commit, and lists each one added.

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

- `fix-check` runs every test not marked `integration` (`pytestmark = pytest.mark.integration`); `verify` runs them all.
- `verify` requires full branch coverage of the source by its own tests. `# pragma: no cover` excludes nothing; only `if TYPE_CHECKING:`, `if __name__ == "__main__":` and `@overload` are exempt. Code a test runs in a subprocess counts.
- Each test file is imported by its path, so no test file imports another: shared test code goes in `conftest.py` fixtures.

## Setup

A project's settings may add, never lower; `wiring` names any setting it refuses. What a project may add:

- A stub package for an untyped library, as a dev dependency.
- The `integration` mark on a test that needs a service.
- `source` under `[tool.py-harness]`, with pytest's `testpaths`, when the code is not in `src/` and the tests not in `tests/`.
- Layers under `[tool.importlinter]`, which the doctor's `boundaries` rule enforces.
- A check of its own in `.py-harness/doctor/`: a `rule-*.py` fails, a `report-*.py` informs.

## Suppressions

A suppression is the user's decision: ask before adding one, and say why. The forms that exist each cover one line, name what they suppress, and fail when they suppress nothing:

| Form | Where |
|---|---|
| `# noqa: CODE` | lint |
| `# pyright: ignore[rule]` | types |
| `cast(T, value)` | types |
| `@pytest.mark.xfail` | tests, strict only |

Anything wider fails: a file-level directive, a bare `noqa` or type ignore, `# fmt: off`, a skipped test.
