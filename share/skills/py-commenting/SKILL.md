---
name: py-commenting
description: Use before writing or editing any comment, docstring, or doc text in Python source.
---

# Commenting

A comment has exactly one reason to change: a change in the thing it annotates. A comment that could go stale while the code beneath it stays the same was describing something else, a caller, a sibling module, a workflow, and leaks it into the wrong place.

Names, decomposition and type hints carry the meaning. A comment records only what they cannot: the non-obvious why. Default to none; when the urge to comment comes, rename or decompose first.

## The test

A comment passes all four, or the code changes instead:

1. **Why, not what.** The code states what it does; the comment states why, only when that is non-obvious.
2. **Non-redundant.** It never restates the name, the signature or the type hints.
3. **Self-contained.** It names nothing outside the file's reach: no consumer, no caller, no "see elsewhere", no example naming things that do not exist.
4. **Durable.** It survives a refactor of the line beneath it.

## Docstrings

None by default. One only when the why is non-obvious, or a caller must handle what the function raises:

```python
"""Short summary."""
```

```python
"""Short summary.

Raises ValueError when the signature is invalid.
"""
```

No `Args:`, `Returns:` or `Parameters` sections: the signature says it. A leading underscore, not prose, marks what is internal.

## By construct

- **File.** No module docstring and no header: no banner, path, author, date or changelog. A file that needs a preface has the wrong name or the wrong split.
- **Section.** `# #region <Name>`, one line. A paragraph of rationale goes on `#` lines under it, never in a drawn banner.
- **Package.** `__init__.py` stays empty: a symbol is imported from the module that defines it.
- **Class.** A docstring only when public-facing: a one-line summary, expanded only with what a consumer needs to use it correctly.
- **Function.** None by default. A dense dispatch, such as a `match` on a discriminant, gets one line; its cases are not narrated.
- **Attribute or constant.** None by default; one line when name and type leave the why unclear: a unit, an invariant, a constraint.
- **Inline.** Rare: one line directly above the statement, stating a non-obvious constraint, an ordering requirement, or a workaround and its cause.

## Library code never names its consumer

A comment in library code describes the unit, never who calls it or why a caller needs it:

```python
# Leaks the layer above.
def validate(token: str) -> None:
    """Validates the token before the auth middleware reads the session."""


# Describes the unit only.
def validate(token: str) -> None:
    """Validates the token.

    Raises ValueError when the signature is invalid.
    """
```

## Signposts

In a longer function, one line above a block may name the step it performs, so a reader sees the shape of the procedure first. When the extracted function's name would say exactly what the signpost says, extract it instead.

## Style

- Third person, present tense, code terminology; a capital letter to start and a full stop to end.
- Acronyms uppercase: URL, ID, HTTP.
- The code as it is: no history, no plan, no "fixed bug", no unfinished-work marker.
- The reasoning itself, never a tool or product cited as authority.
- No em dashes, bold, italics or rhetorical statements.
- A comment changes with its code, in the same commit; deleting the code deletes its comment.

## What the harness checks

- `doctor`, `no-comment-overreach`: a comment or docstring naming what its file neither defines, imports nor quotes. A backticked, `snake_case`, `UPPER_SNAKE` or dotted word counts as a name.
- `lint`, `ERA001`: commented-out code.
- `lint`, `FIX001` to `FIX004`: `TODO`, `FIXME`, `XXX` and `HACK` markers.

Everything else here is judgment no tool checks.
