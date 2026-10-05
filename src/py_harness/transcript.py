import json
import re
from pathlib import Path
from typing import TypeAlias
from typing import cast

from py_harness.tables import as_table

# One line of the client's session transcript.
Event: TypeAlias = dict[str, object]

# Fewer words than this match some message by chance.
QUOTE_WORDS = 3
# A span in straight or curly double quotes.
QUOTED = re.compile(r'["\u201c]([^"\u201d]+)["\u201d]')
UNVERIFIED = "unverified: the client names no transcript to check"


def transcript(payload: dict[str, object]) -> list[Event] | None:
    """The session so far, when the client names its transcript."""
    path = Path(text(payload, "transcript_path"))
    if not path.name or not path.is_file():
        return None
    lines = path.read_text(encoding="utf-8").splitlines()
    # The client writes each event of the session as one JSON object per line.
    return [cast("Event", json.loads(line)) for line in lines if line]


def quoted(passage: str, events: list[Event]) -> str:
    """The user's own words the passage gives, whole or in double quotes; empty when none."""
    candidates = [passage, *(match[1] for match in QUOTED.finditer(passage))]
    found = [candidate for candidate in candidates if not misquoted(candidate, events)]
    return " ".join(found[0].split()) if found else ""


def misquoted(quote: str, events: list[Event]) -> str:
    """Why the quote is not the user's own words; empty when it is."""
    words = " ".join(quote.split())
    if len(words.split()) < QUOTE_WORDS:
        return f"it quotes fewer than {QUOTE_WORDS} words"
    if not any(words in message for message in typed(events)):
        return "the user said none of it"
    return ""


def typed(events: list[Event]) -> list[str]:
    """What the user wrote or picked; text the client adds on its own, such as a skill, is meta."""
    return [
        " ".join(message.split())
        for event in events
        if event.get("type") == "user" and event.get("isMeta") is not True
        for message in (*texts(event), *picked(event))
    ]


def texts(event: Event) -> list[str]:
    """The text in an event's message; tool calls and their results are other blocks."""
    content = as_table(event.get("message")).get("content")
    if isinstance(content, str):
        return [content]
    if not isinstance(content, list):
        return []
    parts = [as_table(part) for part in cast("list[object]", content)]
    return [text(part, "text") for part in parts if part.get("type") == "text"]


def picked(event: Event) -> list[str]:
    """The user's answers to the agent's questions, which reach it as a tool's result."""
    given = as_table(as_table(event.get("toolUseResult")).get("answers"))
    return [value for value in given.values() if isinstance(value, str)]


def text(table: dict[str, object], key: str) -> str:
    value = table.get(key)
    return value if isinstance(value, str) else ""
