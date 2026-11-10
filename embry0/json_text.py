"""Reading JSON from text embry0 did not write: log lines, agent output, files in a repository.

``json.loads`` fails on such text in three ways, and only the first is a
:class:`json.JSONDecodeError`:

- text that is not JSON raises ``JSONDecodeError``, a ``ValueError``;
- an integer longer than the interpreter's digit limit
  (:func:`sys.get_int_max_str_digits`, 4,300 by default) raises a plain
  ``ValueError``, wherever it sits in the document; so do bytes that are
  not UTF-8 (``UnicodeDecodeError``);
- nesting deeper than the interpreter's recursion limit raises
  ``RecursionError``, which is not a ``ValueError`` at all.

A reader that catches only ``JSONDecodeError`` lets the other two escape
its "not JSON" path, so one pathological line in a log window or an agent's
answer fails the whole operation that read it. Catch
``(ValueError, RecursionError)`` instead, or use :func:`json_object`.

The sandbox and proxy images copy a hand-picked list of files, and this
module is not on it: the modules baked into them (``embry0/agents/``,
``embry0/sandbox/``, the proxies) catch the pair inline. An import of it
there fails ``tests/unit/sandbox/test_runner_import_surface.py`` (at module
load) or ``tests/unit/execution/proxy/test_dockerfile_copies_every_proxy.py``.

A document that reads fine can still be too deep to serve:
:data:`SERVABLE_DEPTH` and :func:`nested_deeper_than` are the check a reader
applies before it keeps or relays one, and :func:`served` is what a read
shows of a stored one past :data:`STORED_DEPTH`.
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Iterator, Mapping
from typing import Any

import structlog


def json_object(text: str | bytes | bytearray) -> dict[str, Any] | None:
    """The JSON object ``text`` holds, or ``None`` when it holds none.

    ``None`` for text that is not JSON, JSON that is not an object (a
    number, a list, a string, ``null``) and JSON Python cannot decode (an
    integer past the digit limit, nesting past the recursion limit, bytes
    that are not UTF-8). Never raises for any ``str`` or ``bytes``.
    """
    try:
        parsed = json.loads(text)
    except (ValueError, RecursionError):
        return None
    return parsed if isinstance(parsed, dict) else None


#: The deepest a JSON document may nest and still be served through a
#: response model: pydantic-core stops serializing at its 256th level of
#: containers, and the read answers 500. A document relayed as it is, the
#: whole response, is checked against it.
SERVABLE_DEPTH = 255

#: The deepest a document embry0 stores from outside may nest. It is served
#: back inside a response (a record, a list of them), so it keeps room under
#: :data:`SERVABLE_DEPTH` for the levels around it.
STORED_DEPTH = 200


def nested_deeper_than(value: Any, depth: int) -> bool:
    """Whether ``value`` nests containers (mappings, lists, tuples) more than ``depth`` levels deep.

    ``{}`` and ``[1]`` are one level deep, a scalar none. Walks without
    recursion, so a document nested past the interpreter's own limit is
    answered too.
    """
    stack: list[tuple[Any, int]] = [(value, 1)]
    while stack:
        item, level = stack.pop()
        if isinstance(item, Mapping):
            children: Iterable[Any] = item.values()
        elif isinstance(item, list | tuple):
            children = item
        else:
            continue
        if level > depth:
            return True
        stack.extend((child, level + 1) for child in children)
    return False


#: What a read shows of a stored document too deep even to write as JSON text.
UNSERVABLE_TEXT = "[a document nested too deep to show]"


def served(value: Any) -> Any:
    """``value`` as a read serves it: as it is, or as its JSON text when it nests deeper than :data:`STORED_DEPTH`.

    The write paths refuse such a document (``ServableFields``), but a row
    stored before they did, or written by something other than the API, can
    still hold one; served as it is, it would make its read answer 500, and a
    list would lose every row with it (#431). Its text is what the job events
    page shows of an event too deep to serve. ``value`` itself, never a copy,
    when it is shallow enough.
    """
    if not nested_deeper_than(value, STORED_DEPTH):
        return value
    try:
        return json.dumps(value, ensure_ascii=False, default=str)
    except (ValueError, RecursionError):
        return UNSERVABLE_TEXT


def json_kind(value: Any) -> str:
    """A decoded JSON value's kind in JSON's own words: ``an object``, ``an array``,
    ``a string``, ``a number``, ``true``, ``false`` or ``null``."""
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, Mapping):
        return "an object"
    if isinstance(value, list | tuple):
        return "an array"
    if isinstance(value, str):
        return "a string"
    return "a number"


#: The most ``{`` one scan of free text decodes from (:func:`json_objects_in`).
#: Every ``{`` counts, those inside strings too: a review verdict of 700
#: comments that each quote a few lines of code has about 2,100 from where it
#: starts to the end of the text, one of 1,000 small checks about 3,000.
SCAN_MAX_ATTEMPTS = 8192

#: The most characters the decoder walks over in one scan, all attempts
#: together, per character of the text (and never less than
#: :data:`SCAN_MIN_WORK`). Finding an object walks it once, plus each object
#: nested in it, so real text stays far below it; text nested thousands of
#: levels deep reaches it in a few attempts.
SCAN_WORK_PER_CHAR = 16
SCAN_MIN_WORK = 64 * 1024

#: How much of the text a candidate is first decoded from: a copy this long
#: from the ``{``, doubled while the object may run past its end. A decode
#: error counts the lines before where it stopped, so decoding the whole text
#: in place made each failed candidate cost the length of the text; from a
#: window it costs what the decoder walked.
_SCAN_WINDOW = 4096

logger = structlog.get_logger(__name__)


def _may_be_cut(exc: json.JSONDecodeError, length: int) -> bool:
    """Whether a decode of a window may have failed only because the window ends there.

    The decoder stopped in the window's last few characters (a value, a
    literal, a number or an escape cut short; ``-Infinity`` is the longest),
    or in a string that never closed. Any other error is where the whole
    text fails too.
    """
    return exc.pos >= length - len("-Infinity") or exc.msg.startswith("Unterminated string")


def _decode_at(decoder: json.JSONDecoder, text: str, start: int) -> tuple[Any, int]:
    """The value that starts at ``start`` (None when none decodes there) and the characters it cost."""
    cost = 0
    window = _SCAN_WINDOW
    while True:
        end = min(len(text), start + window)
        doc = text[start:end]
        try:
            value, used = decoder.raw_decode(doc)
        except json.JSONDecodeError as exc:
            if end < len(text) and _may_be_cut(exc, len(doc)):
                cost += len(doc)
                window *= 2
                continue
            return None, cost + exc.pos + 1
        except (ValueError, RecursionError):
            # How far the decoder got is not known: count the whole window.
            return None, cost + len(doc)
        return value, cost + used


def json_objects_in(text: str, *, scanner: str, from_end: bool = False) -> Iterator[dict[str, Any]]:
    """Each JSON object that starts at a ``{`` of ``text``, in the order of where it starts.

    From the last ``{`` back when ``from_end``, so the first object a caller
    takes is the last one in the text. Every ``{`` is a candidate, so an
    object is found bare, fenced or as the whole text, and one nested in
    another is found too; a candidate that does not decode (truncated, not
    JSON, or JSON Python cannot decode) is skipped.

    The cost is bounded whatever the text holds, and grows with its length.
    The decoder starts from at most :data:`SCAN_MAX_ATTEMPTS` candidates and
    walks at most :data:`SCAN_WORK_PER_CHAR` characters per character of the
    text; a scan that stops at either bound reads as if the text held no
    further object, and logs ``json_scan_stopped`` (``scanner`` names the
    caller; the text is never logged). Without the bounds, text of nothing
    but ``{``, or nested thousands of levels deep, cost seconds per 100 KB.
    """
    decoder = json.JSONDecoder()
    budget = max(SCAN_MIN_WORK, SCAN_WORK_PER_CHAR * len(text))
    start = text.rfind("{") if from_end else text.find("{")
    attempts = 0
    while start != -1 and attempts < SCAN_MAX_ATTEMPTS and budget > 0:
        attempts += 1
        obj, cost = _decode_at(decoder, text, start)
        budget -= cost
        if isinstance(obj, dict):
            yield obj
        start = text.rfind("{", 0, start) if from_end else text.find("{", start + 1)
    if start != -1:
        logger.warning(
            "json_scan_stopped",
            scanner=scanner,
            bound="attempts" if attempts >= SCAN_MAX_ATTEMPTS else "work",
            attempts=attempts,
            text_length=len(text),
        )
