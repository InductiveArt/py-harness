---
name: py-commenting
description: Use before writing or editing any comment, docstring, or doc text in Python source.
---

# Commenting

GOLDEN RULE: a comment has exactly one reason to change: a change in the thing it annotates.
If a comment could go stale while the code beneath it stays the same, it was never describing that code. It was describing something else (a caller, a sibling module, a workflow, the codebase at large) and leaking it into the wrong place.

## Principle

Code is the plan. Like a blueprint it must be unambiguous, clear, svelte, and direct.
The name, the decomposition, and the type hints carry the meaning.
A comment exists only to record what those three cannot: the non-obvious why.

A comment is a liability: it rots, misleads, and leaks. Default to none. When the urge to comment appears, the first move is to rename or decompose, not to write prose.

## The test

Every comment must pass all four before it is written. If it fails one, fix the code instead.

1. **Why, not what.** The code states what it does. The comment states why, only when that is non-obvious.
2. **Non-redundant.** If it restates the name, the signature or the type hints, delete it.
3. **Self-contained.** No reference to anything outside the file's reach: no consumers, no callers, no "see elsewhere", no dangling pointer.
4. **Durable.** It survives a refactor of the line beneath it. A comment pinned to a detail that will change is a future lie.

## Docstrings

Default: zero. Add one only when the why is non-obvious or a caller must handle what the function raises.

Allowed forms, and no others:

```python
"""Short summary."""
```

```python
"""Short summary.

Raises ValueError when the signature is invalid.
"""
```

No `Args:`, `Returns:` or `Parameters` sections: the signature and its type hints already say it. Visibility is carried by the name, not by prose: a leading underscore marks what is internal.

## By construct

- **File.** No module docstring and no leading comment block. No banner, no path, no author, no date, no changelog. If a file needs a preface to be understood, its name or its split is wrong.
- **Section.** `# #region <Name>`, one line, to mark a section of a file. Never a drawn banner or a rule of dashes: those carry no information the name does not, and they cost three lines to say one word. When a region needs a paragraph of rationale, the prose goes on `#` lines directly under the marker.
- **Package.** `__init__.py` stays empty. A symbol is imported from the module that defines it, so a package has no surface of its own to document; the README describes the package.
- **Class.** Docstring only when public-facing. One-line summary, expanded only with what helps a consumer use it correctly. No internal narrative, no implementation tour.
- **Function / method.** Default none; name and signature carry it. Add a docstring when the why is non-obvious or a raised exception is part of the contract. A dense dispatch (a `match` on a discriminant) gets a one-line docstring; do not narrate each case.
- **Attribute / constant.** Default none. One line only when name and type leave the why unclear (a unit, an invariant, a constraint).
- **Inline.** Rare. A single line directly above the statement it explains, stating the why: a non-obvious constraint, an ordering requirement, or a workaround and its cause. Never narrate the obvious. Never trail an essay off the end of a line.

## Library rule: never name the consumer

In library code a comment describes the unit, never who calls it or why some caller needs it. Naming a consumer leaks the abstraction downward and binds the library to a layer above it.

```python
# Bad: leaks the layer above.
def validate(token: str) -> None:
    """Validates the token before the auth middleware reads the session."""


# Good: describes the unit only.
def validate(token: str) -> None:
    """Validates the token.

    Raises ValueError when the signature is invalid.
    """
```

## Signpost comments

Inside a longer function, a one-line comment may sit above a block of related lines to name the step it performs. Its job is not to explain a hard line; it chunks a procedure into labelled phases so a reader can scan the shape of the function before reading its detail. Prefer decomposition when the extracted function's name would state exactly what the signpost said; keep the signpost when splitting would only scatter the procedure.

## Style

- Third person, professional, code terminology.
- Capital letter to start, full stop to end.
- Acronyms uppercase: URL, ID, TTL, HTTP.
- One reading only; no ambiguity.
- Present tense, describing the code as it is: not its history, not a plan.
- No em-dashes, no bold, no italics, no rhetorical statements.

## Maintenance

- A comment changes with its code, in the same commit. A stale comment is a bug.
- Delete the code, delete the comment with it.
- No changelogs, no commented-out code, no "fixed bug" archaeology. History lives in version control.
- No unfinished-work markers. Work that is not done is either done now or recorded outside the code.

## Anti-patterns: delete on sight

- Storytelling prose narrating what the next line plainly does.
- Restating the name, the signature or the type hints in words.
- Referencing a consumer, caller, or external system from library code.
- Vague pointers with no in-file anchor.
- Illustrative code inside a comment: an example names things that do not exist.
- Drawn banners, rules of dashes, ASCII art. A section is `# #region <Name>`.
- File headers carrying author, date, path, or changelog.
- `Args:` / `Returns:` sections echoing the signature.
- Commented-out code kept "just in case".
- Citing a tool or product as authority ("the way framework X does it"); state the reasoning itself.

## What the harness checks

- `make doctor RULE=no-comment-overreach`: a comment or docstring naming what its file neither defines, imports nor quotes. A word counts as a name when it is backticked, `snake_case`, `UPPER_SNAKE` or dotted; URLs, abbreviations and plain capitalised words do not.
- `make lint`, rule `ERA001`: commented-out code.
- `make lint`, rules `FIX001` to `FIX004`: `TODO`, `FIXME`, `XXX` and `HACK` markers.

Everything else in this skill is judgment no tool checks.
