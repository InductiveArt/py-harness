# py-harness

One quality gate for Python repositories worked on by agents and people:
ruff, basedpyright, pytest with full branch coverage and structural rules
behind two `make` commands, with the rules and skills that tell an agent how
to work with them. A repository points at the harness; nothing is copied into
it.

## Install

For a repository holding one project; uv workspaces are not supported yet.
In `pyproject.toml`, pinned to a release:

```toml
[dependency-groups]
dev = ["py-harness"]

[tool.uv.sources]
py-harness = { git = "https://github.com/InductiveArt/py-harness", tag = "v0.1.0" }

[tool.ruff]
extend = ".venv/share/py-harness/ruff.toml"

[tool.basedpyright]
extends = ".venv/share/py-harness/pyrightconfig.json"
```

In the `Makefile`:

```make
include .venv/share/py-harness/harness.mk
```

Then, once per clone:

```sh
uv sync      # make cannot read the include before .venv exists
make agent   # gives the agent its rules and skills
```

`make agent` links `.claude/rules/py-harness.md` and the `py-quality-tooling`
and `py-commenting` skills into the installed harness, and lists them in
`.git/info/exclude`, so no commit carries them. `fix-check` and `verify` run
it too. Claude Code reads them as they are; Copilot in VS Code reads them once
`chat.useClaudeMdFile` is on.

Releases are this repository's tags. To update, set `tag` to a newer one and
run `uv sync`. The lockfile pins the commit a project runs, ruff and
basedpyright with it, so nothing moves until the project updates.
`make update` upgrades every other dependency.

## Use

| Command | Answers | Runs | Changes files |
|---|---|---|---|
| `make fix-check` | Did I break what I just touched? | the agent links, wiring, lint and format fixes, then format, lint, typecheck, doctor, unit tests | yes |
| `make verify` | Does all of it pass? | install, the agent links, wiring, format, lint, typecheck, doctor, every test at full branch coverage | no |

Run `fix-check` after each change and `verify` before calling the work done.

A run prints one line per stage, a block for each stage that did not pass,
and its verdict last:

```
  lint        failed   102 findings in 31 files (0.2s)
  typecheck   ok       2406 recorded (3.8s)
  test        ok       442 passed, 0 failed (8.2s)
```

- A failed stage found something in the code. Its block shows the first
  findings and names the file holding all of them, one `## <path>` section
  per file or test.
- A broken stage did not finish: its tool crashed, its report could not be
  read, or it ran past 900 seconds (`PY_HARNESS_STAGE_SECONDS`). Its result is
  unknown.
- Everything is kept in `.git/py-harness/logs/<command>/`: each stage's output
  in `<stage>.log`, headed by its summary line; its findings in `<stage>.txt`;
  the run in `run.json`. `make last` prints the newest summary again.

`make help` lists every target, each stage included.

## Adopting existing code

Code the harness reached late passes after four steps, each a commit of its
own. In a codebase that already passes, there is nothing to do in any of them.

0. **The wiring**: the `pyproject.toml`, `uv.lock` and `Makefile` changes
   from Install, so everyone working on the repository runs the same gate.
1. **Type errors**, when fixing them would be a project of its own:
   `make baseline` records them in `.basedpyright/baseline.json`; commit it.
   `typecheck` then fails only on new errors. An error is matched by its file,
   its rule and its place on its line, never by line number; a fixed one
   leaves the file on the next run that has no new error. Every run's footer
   counts the recorded errors, with those added and fixed since the last
   commit, and lists each one added, so the file never grows or shrinks
   unseen.
2. **Mechanical fixes**: `make fix-check` once, its rewrite committed alone,
   so later runs rewrite only what a change touches.
3. **The findings left**: lint, doctor and coverage findings are fixed, not
   recorded, one stage at a time. `make lint-fix-unsafe` clears part of the
   lint; read its diff.

## Configuration

A project's own settings may add rules and describe its environment, never
lower the shared bar. `wiring` refuses an `ignore`, a per-file ignore, a
replaced `select`, a raised threshold, a basedpyright rule or mode below the
shared one, a pytest setting that changes which tests run or how they count,
basedpyright or pytest settings outside the root `pyproject.toml`, and any
coverage settings, since coverage always runs on the harness's.

- **Layout.** Code in `src/`, tests in `tests/`. A project laid out otherwise
  says so once:

  ```toml
  [tool.py-harness]
  source = "src/main/python"

  [tool.pytest.ini_options]
  testpaths = ["src/test/python"]
  ```

- **Integration tests** are marked `integration`
  (`pytestmark = pytest.mark.integration`). `fix-check` leaves them out;
  `verify` runs them.
- **Boundaries**, which module may import which, go in `[tool.importlinter]`,
  and the doctor's `boundaries` rule enforces them.
- **Own doctor checks** go in `.py-harness/doctor/`: `rule-*.py` and
  `drift-*.py` fail, `report-*.py` informs. Each is a script. Exit 0 passes,
  printing `doctor: <name> OK`; exit 1 fails, printing a heading that ends in
  a colon, then one indented line per finding; any other exit is broken. The
  units arrive in `PY_HARNESS_UNITS`, one path per line, and the harness's
  folder in `PY_HARNESS_DIR`.
- **`.py-harness/ignore`** lists units no stage covers; every run says so.

## Agent gates

Optional. Turn them on once `make fix-check` passes; before that, they would
send the agent after every existing finding.

The **Stop gate**: when the agent stops with uncommitted changes and
`fix-check` fails, the summary goes back to it, once per stop. In
`.claude/settings.json`:

```json
{
  "hooks": {
    "Stop": [
      {
        "hooks": [
          {
            "type": "command",
            "command": "uv run --no-sync python -m py_harness.stop_gate",
            "timeout": 900
          }
        ]
      }
    ]
  }
}
```

The **change gate**: a destructive command (`rm`, `git reset --hard`, a
force-push, ...) runs only when the agent's description quotes you asking for
it; a new file, or an edit to `pyproject.toml` or the `Makefile`, is held once
for the agent to state the facts that bear on it; and at the end of each turn,
git shows you every new file, deletion and wiring edit. In
`.claude/settings.local.json`:

```json
{
  "hooks": {
    "PreToolUse": [
      {
        "matcher": "Write|Edit|MultiEdit|Bash",
        "hooks": [
          {
            "type": "command",
            "command": "uv run --no-sync python -m py_harness.change_gate",
            "timeout": 60
          }
        ]
      }
    ]
  }
}
```

For Copilot in VS Code, link `.venv/share/py-harness/hooks/vscode.json`, and
`vscode-changes.json` for the change gate, into `.github/hooks/`, and set
`"chat.useHooks": true` in `.vscode/settings.json`; VS Code runs them only in
a trusted workspace. `make audit` prints every gate decision.

## Not covered

- Secrets and vulnerable dependencies: catch them where code leaves the
  machine, with push protection, a secret scanner and a dependency audit.
- A comment's prose: `no-comment-overreach` checks the names a comment
  mentions, and the `py-commenting` skill carries the rest.
- Code only its own tests call: coverage counts tests, so nothing finds an
  unused public function.
- Names read through `vars()`, `__dict__` or `attrgetter`, and a
  `conftest.py` hook that deselects tests.
- The change gate checks that a quote is your words, not that they ask for
  this command. It misses an untracked file deleted by a command its patterns
  do not know, and counts your own edits during a turn as the session's. In
  VS Code, its messages and the agent's quotes are untested.

## License

MIT. See `LICENSE`.
