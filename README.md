# py-harness

Shared build and health tooling for Python repositories: make targets, tool
configs, structural rules and the agent skills that describe them, consumed
by `extend`, `include` and links, never copied.

## Purpose

Every repository gets the same bar from one versioned artifact. Nothing is
copied into a consuming repository, so nothing drifts out of step with it.
Upgrading is a version bump, and the lockfile pins the harness together with
every tool it runs.

## Use

Add it as a dev dependency and point each tool at the files it installs:

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

```make
include .venv/share/py-harness/harness.mk
```

The first `make check` gives the agent the harness's rules and skills: it
links `.claude/rules/py-harness.md`, `.claude/skills/py-quality-tooling` and
`.claude/skills/py-commenting` into the harness version the lockfile pins, and
lists them in `.git/info/exclude`, so no commit carries them. Claude Code reads
them as they are; Copilot in VS Code reads them once `chat.useClaudeMdFile` is
on in its settings, whichever model it runs. The rules tell the agent to run
`make check` after every change and never to silence a finding; the skills
explain each target and the fix each finding expects.

Once `make check` passes, a Stop hook can hold the agent to it: when it stops
with uncommitted changes and the check fails, the summary goes back to it and
it keeps working, once per stop. In `.claude/settings.json`:

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

For Copilot's agent in VS Code, the harness ships the same gate in VS Code's
own hook format. Link it, and enable hooks in the committed workspace settings
(`"chat.useHooks": true` in `.vscode/settings.json`); VS Code runs workspace
hooks only in a trusted workspace:

```sh
mkdir -p .github/hooks
ln -s ../../.venv/share/py-harness/hooks/vscode.json .github/hooks/py-harness.json
```

Before the project passes, the hook would send the agent after every existing
finding instead of the task at hand, so it waits until then.

A second hook, the change gate, is opt-in the same way. It watches three
kinds of change: a new file, an edit to the harness wiring (`pyproject.toml`,
the Makefile), and a destructive command (`rm`, `git reset --hard`, a
force-push, ...).

- A destructive command runs only when its description quotes you asking for
  it: three words at least, found in what you typed or picked in answer to the
  agent's question. A command you never asked for has nothing to quote, so the
  agent has to ask you first.
- A new file or a wiring edit made with a file tool is held once, for the
  agent to state the facts that bear on it: who will use a new file and which
  existing module could hold it instead. The facts are for the agent's own
  context; none of it is checked, and the retry goes through.
- When a turn ends, the Stop gate compares the working tree with how the
  session found it and shows you every new file, deletion and wiring edit,
  however it was made, shell commands included. It comes from git, not from
  the agent, so it cannot leave one out. In `.claude/settings.local.json`:

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

For VS Code, link `.venv/share/py-harness/hooks/vscode-changes.json` into
`.github/hooks/` beside the Stop gate's.

Both gates record every decision in the clone's audit trail,
`.git/py-harness/audit.jsonl`, never committed; when the change gate lets a
destructive command through, the trail keeps your words it quoted, and it
keeps every change the Stop gate showed you.
`make audit` prints the trail.

Run `uv sync` once first: make cannot read the include before `.venv` exists.
Then `make help` lists every target. The composed ones are named for the
question each answers: `check` is "did I break what I just touched", `ready`
is "is this ready to commit". Both fix before they judge, as an agent at work
wants. `ci` is "does the code as committed pass": the same checks as `ready`,
never rewriting a file, so what a fix would repair fails instead. Any CI server
runs it the same way, as a Jenkins stage, a GitHub Actions step or a GitLab
job:

```sh
uv sync --locked
make ci
```

A composed run prints one line per stage as it ends, then a block for each
stage that did not pass, and its verdict on the last line. A test stage's line
says how many tests passed and failed and, for `coverage`, how much of which
source they run: `442 passed, 0 failed, 100% of src`. A stage that
failed found something in the code: its block counts the findings per rule,
shows the first few, and names the file holding all of them, one section per
source file or test. A stage that broke did not finish: its checker crashed,
its report could not be read, or it ran past 900 seconds
(`PY_HARNESS_STAGE_SECONDS` changes the limit). Its result is unknown, and its
block shows its last lines instead. A pass is never taken on a stage's word
alone: its exit code must agree. Each stage's whole output goes to its log,
beside the findings files, in `.git/py-harness/logs/<loop>/`, where no commit
carries them; `make last` prints the newest run again from there without
running anything. Only `ci` also prints every stage's output, since a CI
server's console is the only log it keeps.

Each unit keeps its code in `src/` and its tests in `tests/` by default. A unit
laid out otherwise declares it once, in its own `pyproject.toml`: the code
under `[tool.py-harness] source`, the tests in pytest's own `testpaths`.
Integration tests are the ones marked `integration`. The units of a uv
workspace are its members, then the root when the root is a project.
`make units` shows them.

A repository's own rules go in `.py-harness/doctor/`, as `rule-*.py`,
`drift-*.py` or `report-*.py`, and run beside the shared ones. A unit listed in
`.py-harness/ignore` is covered by no stage, and says so every run. A
repository's boundaries, which module may import which, go in
`[tool.importlinter]`.

## Don't use

- `[tool.coverage]`, `.coveragerc`, `pyrightconfig.json`, `[tool.pyright]`, or
  `[tool.basedpyright]` anywhere but the root `pyproject.toml`. None of them is
  ever read, so each would mislead; `wiring` refuses to run beside them.
- Loosening a shared rule from the repository's own config: an `ignore`, a
  per-file ignore, a replaced `select`, a raised threshold, or a basedpyright
  rule or mode below the shared one. `wiring` refuses each of them; the only
  suppression is an inline, rule-named one, and it fails when it suppresses
  nothing.
- pytest settings outside `pyproject.toml` (`pytest.ini`, a `[pytest]` section
  in `tox.ini`, `[tool:pytest]` in `setup.cfg`), or ones that change which
  tests run or how they count (`addopts`, `python_files`, `norecursedirs`,
  `xfail_strict`). `wiring` refuses each.

## Tradeoff

The harness pins exactly the tools whose version decides a verdict, ruff and
basedpyright, so a repository cannot move them on its own; it moves the
harness. The tools that only run the checks take compatible ranges, and each
repository's lockfile records the versions it runs. Coverage is full branch coverage with no
exclusion comment, so an existing codebase reaches it before `ready` can pass.

## Limits

What no check catches, so a passing run does not claim it:

- A comment's prose. `no-comment-overreach` checks the names a comment
  mentions, not what its words describe; the `py-commenting` skill carries the
  rest.
- Code only its own tests call. Coverage counts tests, so nothing finds a
  public function no production path uses.
- Vulnerable dependencies. `security-scan` is ruff's code-pattern rules;
  nothing audits the locked dependencies.
- Secrets. No stage looks for credentials: catch them where code leaves the
  machine, with push protection or a secret scanner in a pre-commit hook.
- Undeclared layers. `boundaries` enforces the boundaries a repository
  declares, and says when it declares none.
- Names read through `vars()`, `__dict__` or `attrgetter`. `no-hidden-names`
  finds the other ways a name is hidden.
- A `conftest.py` hook that deselects tests. Settings that do so are refused;
  code that does is not.
- Whether a quote fits the change. The gate checks that the words are yours,
  not that they ask for this command.
- Holds in a client that names no transcript. The retry goes through, marked
  unverified in the trail.
- The list of changes in the editor. It reaches you through the client's
  `systemMessage`, which no editor session has shown yet.
- An untracked file deleted by a command the destructive patterns miss. The
  end of the turn cannot see it: git never knew the file.
- Changes you make yourself while the agent works. The end of the turn counts
  them as the session's.
- The editor agent's quote for a destructive command. It is read from the
  terminal tool's `explanation` field, which no editor session has shown yet.

## Invariants

- No stage passes having covered nothing: resolving zero units is an error
  (`src/py_harness/units.py`).
- No stage runs on a config that fell back to its defaults: every target whose
  verdict depends on one runs `wiring` first (`share/harness.mk`).
- No repository config lowers a shared rule: its ruff and basedpyright settings
  may only add rules or describe the environment (`src/py_harness/wiring.py`).
- Every bypass is counted: each run reports how many the repository holds and
  how many the change since the last commit added (`src/py_harness/suppressions.py`).
- Every suppression suppresses something: unused `noqa`, unused type ignores,
  and passing `xfail` tests all fail.
- The agent reads the rules and skills of the harness version it runs: `make
  check` links them and never copies them, and a file in a link's place fails
  the step (`src/py_harness/agent.py`).
- A search for a name finds every use: each symbol is imported under its own
  name, one per line, and no name is built at runtime (`no-hidden-names`).
- No file or region exempts itself from a check; only a single line can be
  suppressed, naming its rule (`no-blanket-exemptions`).
- Every target, shared rule and checklist item names the test that proves it
  (`tests/test_ledger.py`).
- The harness passes its own `make ready` and `make ci`; this repository's
  `Makefile` includes `share/harness.mk`.

## Pointers

- `share/`: the files tools read by path, installed at `.venv/share/py-harness/`.
- `src/py_harness/`: the modules each target dispatches to.
- `src/py_harness/rules/`: the shared doctor rules.
- `share/agent-rules.md`: the rules every agent session loads.
- `share/skills/`: the agent skills; `py-quality-tooling` is the working guide
  to every target, and `py-commenting` the rules `no-comment-overreach` enforces
  in part.
- `tests/test_ledger.py`: what is proven, and where.

## License

MIT. See `LICENSE`.
