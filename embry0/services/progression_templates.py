"""The one template grammar of the content pack's texts (#176), and the one formatter for it.

A pack's texts (an achievement's "what counts", a quest's steps and reward,
a diary task, the chat box's messages, the game's nouns) may name values in
braces. The grammar is deliberately tiny, so an admin's text can never make
a read slow or fail:

- ``{name}``: the value of ``name``, as text; ``name`` is a Python identifier.
- ``{name:,}``: a number with thousands separators, ``1,000``.
- ``{{`` and ``}}``: a literal brace.

Anything else in braces (a conversion such as ``{name!r}``, another format
spec, a nested field, an index, an empty or numbered field) or a lone brace is
refused when the pack is checked. The dashboard fills the same grammar in
``frontend/src/lib/gamePack.ts``, and ``docs/contracts/progression.json``
carries cases both sides are tested against.

The salvage commit's subject template (the settings document's
``salvage.commit_subject``, #182) is written in the same grammar and filled
by the same :func:`render`.

Standard library only, so the vocabulary, the schema, the services and the
settings sections can all import it.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass

#: One token: an escaped brace, a field, or a run of other text.
_TOKEN = re.compile(r"\{\{|\}\}|\{([A-Za-z_][A-Za-z0-9_]*)(:,)?\}|[^{}]+")


class TemplateError(ValueError):
    """A text that is not in the grammar."""


@dataclass(frozen=True)
class Field:
    name: str
    #: ``{name:,}``: thousands separators.
    grouped: bool


def parse(text: str) -> list[str | Field]:
    """A text as its literal runs and its fields; raises :class:`TemplateError` outside the grammar."""
    parts: list[str | Field] = []
    position = 0
    while position < len(text):
        match = _TOKEN.match(text, position)
        if match is None:
            raise TemplateError(
                f"column {position + 1}: a brace starts {{name}} or {{name:,}}; write {{{{ or }}}} for a literal brace"
            )
        token = match.group(0)
        if token == "{{":
            parts.append("{")
        elif token == "}}":
            parts.append("}")
        elif match.group(1) is not None:
            parts.append(Field(match.group(1), match.group(2) is not None))
        else:
            parts.append(token)
        position = match.end()
    return parts


def fields(text: str) -> set[str]:
    """The names a text uses; raises :class:`TemplateError` outside the grammar."""
    return {part.name for part in parse(text) if isinstance(part, Field)}


def _value(value: object, grouped: bool) -> str:
    """A value as text, the way the dashboard writes it: ``1.0`` is ``1``, ``True`` is ``true``."""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, float) and value.is_integer():
        value = int(value)
    if isinstance(value, int):
        return f"{value:,}" if grouped else str(value)
    return str(value)


def render(text: str, values: Mapping[str, object]) -> str:
    """``text`` with its fields filled from ``values``.

    A text that was checked never fails here; one that somehow is not in the
    grammar, or names a value that is missing, is returned with that part as
    written, so a read is never broken by a text.
    """
    try:
        parts = parse(text)
    except TemplateError:
        return text
    out: list[str] = []
    for part in parts:
        if isinstance(part, str):
            out.append(part)
        elif part.name in values:
            out.append(_value(values[part.name], part.grouped))
        else:
            out.append("{" + part.name + (":," if part.grouped else "") + "}")
    return "".join(out)


#: Cases the server's :func:`render` and the dashboard's ``fill`` are both tested against.
CASES: tuple[tuple[str, dict[str, object], str], ...] = (
    (
        "{character} reached {skill} level {level}.",
        {"character": "Forge", "skill": "Coding", "level": 42},
        "Forge reached Coding level 42.",
    ),
    ("A character at total level {total_level:,}", {"total_level": 1000}, "A character at total level 1,000"),
    ("{n:,} and {n}", {"n": 1234567}, "1,234,567 and 1234567"),
    ("{{braces}} stay {name}", {"name": "x"}, "{braces} stay x"),
    ("nothing to fill", {}, "nothing to fill"),
    ("{missing} stays", {}, "{missing} stays"),
    ("{label:,}", {"label": "text"}, "text"),
    (
        "under {max_usd} dollars, {hours:,} hours, {ratio}",
        {"max_usd": 1.0, "hours": 2400.0, "ratio": 2.5},
        "under 1 dollars, 2,400 hours, 2.5",
    ),
    ("{flag}", {"flag": True}, "true"),
)
