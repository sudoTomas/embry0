"""JSON text Python cannot decode, for the readers of text embry0 did not write.

Both shapes are well-formed JSON; ``json.loads`` still refuses them, and not
with a ``JSONDecodeError`` (see ``embry0/json_text.py``):

- **deep**: nesting far past the decoder's recursion limit (about 10,000
  levels on CPython 3.12) raises ``RecursionError``;
- **huge**: an integer longer than CPython's default 4,300-digit limit on
  int conversion raises a plain ``ValueError``.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

import pytest

DEPTH = 100_000
#: A JSON array nested :data:`DEPTH` levels deep, closed: valid, and too deep to decode.
DEEP_ARRAY = "[" * DEPTH + "]" * DEPTH
HUGE_INTEGER = "9" * 5_000

KINDS = ("deep", "huge")


def unreadable_object(kind: str, fields: dict[str, Any] | None = None) -> str:
    """A JSON object holding ``fields``, then one value Python cannot decode."""
    value = {"deep": DEEP_ARRAY, "huge": HUGE_INTEGER}[kind]
    head = json.dumps(fields or {})[:-1]
    return f'{head}{", " if fields else ""}"unreadable": {value}}}'


@contextmanager
def handled(reader: str, *, own: tuple[type[BaseException], ...] = ()) -> Iterator[None]:
    """Fail the test, naming ``reader``, when the decoder's exception escapes it.

    ``own`` are the exceptions ``reader`` raises on purpose for text it cannot
    read (its own "not readable" result); they pass through, for a
    ``pytest.raises`` around this block to check.
    """
    try:
        yield
    except own:
        raise
    except (RecursionError, ValueError) as exc:
        pytest.fail(f"{reader} let {type(exc).__name__} escape on JSON Python cannot decode: {str(exc)[:100]}")
