"""A refused test setup, explained in sentences instead of parser output.

``parse_qa_yaml_v2`` raises a pydantic ``ValidationError`` or a PyYAML error
whose text names schema classes, error types and docs URLs. These helpers
turn each error into one sentence a person can act on, saying where it is
('app web', 'defaults', 'workspace_provider').

Two readers, two forms. :func:`plain_errors` names the keys and values of
the text, for the agent that repairs a setup it holds. :func:`answered_errors`
is what the API answers a refused write with: where (the setup's own words,
an app or package by its position) and what kind, repeating no key, name or
value the text holds.
"""

from __future__ import annotations

import difflib
import types
from collections.abc import Sequence
from typing import Any, Union, get_args, get_origin

import yaml
from pydantic import BaseModel, ValidationError

from embry0.text_documents import unquoted
from embry0.workflows.qa.qa_yaml_v2 import QAYamlConfigV2

# Where a problem sits when it is not inside one section of the setup.
WHOLE_SETUP = "setup"

_URL_FIELDS = frozenset({"frontend_url", "http"})


def plain_errors(exc: BaseException) -> list[str]:
    """One plain sentence per error in ``exc``, in the order raised."""
    return [sentence for _where, sentence in located_plain_errors(exc)]


def located_plain_errors(exc: BaseException) -> list[tuple[str, str]]:
    """``(where, sentence)`` per error in ``exc``.

    ``where`` is the plain location ('app web', 'line 3'), or ``WHOLE_SETUP``
    when the error belongs to no one section.
    """
    if isinstance(exc, ValidationError):
        return [_pydantic_error(err) for err in exc.errors()]
    if isinstance(exc, yaml.YAMLError):
        return [_yaml_error(exc)]
    return [(WHOLE_SETUP, str(exc))]


def _pydantic_error(err: Any) -> tuple[str, str]:
    loc = tuple(err.get("loc") or ())
    kind = err.get("type")
    msg = str(err.get("msg") or "")
    if kind == "extra_forbidden" and loc:
        key, parent = str(loc[-1]), loc[:-1]
        sentence = f"'{key}' is not a setting embry0 knows."
        closest = difflib.get_close_matches(key, _field_names_at(parent), n=1)
        if closest:
            sentence += f" Did you mean '{closest[0]}'?"
        return _with_where(sentence, parent)
    if kind == "missing" and loc:
        return _with_where(f"'{loc[-1]}' is missing", loc[:-1])
    if kind == "value_error" and loc and loc[-1] in _URL_FIELDS and "http(s)" in msg:
        return _with_where("Start the address with http:// or https://", loc[:-1])
    return _with_where(msg.removeprefix("Value error, "), loc)


def answered_errors(exc: BaseException, text: str) -> list[str]:
    """One sentence per error in ``exc``, the setup ``text`` refused for, saying where and what kind of problem it
    is and repeating nothing the text holds: an app, a package or an ``app_local`` entry is said by its position
    ('app 2'), a key embry0 does not know by the nearest one it does, and a value or name a check quotes as
    ``'…'``. For the API's 422; the repair prompts keep :func:`plain_errors`."""
    if isinstance(exc, ValidationError):
        try:
            data = yaml.safe_load(text)
        except (yaml.YAMLError, RecursionError):
            data = None
        return [_answer(err, data) for err in exc.errors()]
    if isinstance(exc, yaml.YAMLError):
        return [_yaml_error(exc)[1]]
    return [_masked(str(exc))]


def _answer(err: Any, data: Any) -> str:
    loc = tuple(err.get("loc") or ())
    kind = err.get("type")
    msg = str(err.get("msg") or "")
    if kind == "extra_forbidden" and loc:
        sentence, at = "Not a setting embry0 knows.", loc[:-1]
        closest = difflib.get_close_matches(str(loc[-1]), _field_names_at(at), n=1)
        if closest:
            sentence += f" Did you mean '{closest[0]}'?"
    elif kind == "missing" and loc:
        sentence, at = f"'{loc[-1]}' is missing", loc[:-1]
    elif kind == "value_error" and loc and loc[-1] in _URL_FIELDS and "http(s)" in msg:
        sentence, at = "Start the address with http:// or https://", loc[:-1]
    elif kind in _CHECKS_SAY:
        # A check of the setup's own words its message, and quotes what was written in it.
        sentence, at = _masked(msg.removeprefix("Value error, ").removeprefix("Assertion failed, ")), loc
    else:
        # pydantic's own messages say what the schema expects, never the input.
        sentence, at = msg, loc
    where = _answered_location(at, data)
    return f"{sentence} ({where})" if where else sentence


#: The error types whose message a check of the setup wrote, which may quote what the text holds.
_CHECKS_SAY = frozenset({"value_error", "assertion_error"})
#: Quoted words a check's message uses of the glob grammar itself, not of what was written.
_GRAMMAR = frozenset({"..", "**"})
#: How a place says an entry of a mapping whose keys the setup names, by its position.
_ENTRY = {"apps": "app", "packages": "package", "app_local": "app_local entry"}


def _masked(text: str) -> str:
    """``text`` with each quoted part said as ``'…'``, unless it is one character or a word of the glob grammar."""
    return unquoted(text, keep=_GRAMMAR)


def _answered_location(loc: Sequence[Any], data: Any) -> str:
    """Where ``loc`` is, in the schema's words, with an entry of a mapping the setup names said by its position in
    ``data`` (the text as read): ('apps', 'web', 'ready_checks', 0) -> 'app 1, ready_checks 1'."""
    current: Any = QAYamlConfigV2
    value: Any = data
    words: list[str] = []
    for part in loc:
        current = _without_none(current)
        if isinstance(current, type) and issubclass(current, BaseModel):
            field = current.model_fields.get(str(part))
            if field is None:
                break
            words.append(str(part))
            current = field.annotation
            value = value.get(str(part)) if isinstance(value, dict) else None
        elif get_origin(current) is dict and words:
            names = list(value) if isinstance(value, dict) else []
            entry = _ENTRY.get(words[-1], f"{words[-1]} entry")
            words[-1] = f"{entry} {names.index(part) + 1}" if part in names else f"an {entry}"
            current = get_args(current)[-1]
            value = value.get(part) if isinstance(value, dict) else None
        elif get_origin(current) is list and isinstance(part, int) and words:
            words[-1] += f" {part + 1}"
            current = get_args(current)[-1]
            value = value[part] if isinstance(value, list) and part < len(value) else None
        else:
            break
    return ", ".join(words)


def _with_where(sentence: str, loc: Sequence[Any]) -> tuple[str, str]:
    where = _plain_location(loc)
    if not where:
        return WHOLE_SETUP, sentence
    return where, f"{sentence} ({where})"


def _plain_location(loc: Sequence[Any]) -> str:
    """('apps', 'web', 'ready_checks', 0) -> 'app web, ready_checks 1'."""
    parts = list(loc)
    words: list[str] = []
    if len(parts) >= 2 and parts[0] == "apps":
        words.append(f"app {parts[1]}")
        parts = parts[2:]
    for part in parts:
        if isinstance(part, int) and words:
            words[-1] += f" {part + 1}"
        else:
            words.append(str(part))
    return ", ".join(words)


def _field_names_at(loc: Sequence[Any]) -> list[str]:
    """Setting names the schema accepts at ``loc``; empty when it is not a section."""
    current: Any = QAYamlConfigV2
    for part in loc:
        current = _without_none(current)
        if isinstance(current, type) and issubclass(current, BaseModel):
            field = current.model_fields.get(str(part))
            if field is None:
                return []
            current = field.annotation
        elif get_origin(current) in (dict, list):
            current = get_args(current)[-1]
        else:
            return []
    current = _without_none(current)
    if isinstance(current, type) and issubclass(current, BaseModel):
        return list(current.model_fields)
    return []


def _without_none(annotation: Any) -> Any:
    """``X | None`` -> ``X``; anything else unchanged."""
    if get_origin(annotation) in (Union, types.UnionType):
        members = [a for a in get_args(annotation) if a is not type(None)]
        if len(members) == 1:
            return members[0]
    return annotation


def _yaml_error(exc: yaml.YAMLError) -> tuple[str, str]:
    mark = None
    if isinstance(exc, yaml.MarkedYAMLError):
        mark = exc.problem_mark or exc.context_mark
    if mark is None:
        return WHOLE_SETUP, "The text could not be read."
    line = mark.line + 1
    if _starts_with_tab(mark):
        return f"line {line}", f"Line {line} starts with a tab. Indent with spaces."
    return f"line {line}", f"The text could not be read near line {line}."


def _starts_with_tab(mark: Any) -> bool:
    """The mark sits on a tab inside the line's leading whitespace."""
    buffer = getattr(mark, "buffer", None)
    if not isinstance(buffer, str):
        return False
    lines = buffer.split("\n")
    if mark.line >= len(lines):
        return False
    indent = lines[mark.line][: mark.column + 1]
    return indent.endswith("\t") and not indent.strip()
