---
applyTo: "**"
---

# Working in a repository checked by py-harness

- After changing code, run `make fix-check`. Fix what its summary names and run it again until it passes; re-read any file it lists as changed before editing that file again. Run `make verify` before saying the work is done.
- Fix a finding; never silence it. Rewording code or a comment so a check stops matching it is silencing it too. A `noqa`, a `pyright: ignore`, a skipped test, a type error recorded by `make baseline` or a unit listed in `.py-harness/ignore` is the user's decision: ask before adding one, and say why. Every run lists the suppressions added since the last commit.
- A whole-word search for a name finds every use of it: each symbol keeps its own name wherever it is used, is never re-exported or built at runtime, and each import line names the module it comes from. Search a message seen in output by a few of its words; a long one can span source lines.
- Load the `py-quality-tooling` skill before running any make target: it states the workflow for each situation and the fix each finding expects. Load the `py-commenting` skill before writing any comment or docstring.
