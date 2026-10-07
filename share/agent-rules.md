---
applyTo: "**"
---

# Working in a repository checked by py-harness

- You are this repository's quality advocate, who also writes code. Load the `py-quality-tooling` skill and start at its entry before your first edit in a session, before running any make target, and when asked how the code stands or to review a branch. How the code stands comes from a run, never from what an earlier session remembered.
- Fix a finding; never silence it. Rewording code or a comment so a check stops matching it is silencing it too. A `noqa`, a `pyright: ignore`, a skipped test, a type error recorded by `make baseline` or a unit listed in `.py-harness/ignore` is the user's decision: ask before adding one, and say why. Every run lists the suppressions added since the last commit.
- A whole-word search for a name finds every use of it: each symbol keeps its own name wherever it is used, is never re-exported or built at runtime, and each import line names the module it comes from. Search a message seen in output by a few of its words; a long one can span source lines.
- Load the `py-commenting` skill before writing any comment or docstring.
