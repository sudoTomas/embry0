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
applies before it keeps or relays one.
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Mapping
from typing import Any


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
