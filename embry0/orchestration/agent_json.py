"""The JSON object an agent writes at the end of its final text.

Agents narrate, then write their result as a JSON object: bare after the
prose, inside a ```json fence, or as the whole text. The executors join every
assistant message into one output, so the object is rarely the whole text and
``json.loads(output)`` misses it. One reader for all of them.
"""

from __future__ import annotations

import json
from typing import Any


def last_object_with(text: str, key: str) -> dict[str, Any] | None:
    """The last JSON object in ``text`` that has ``key``, else None.

    Every ``{`` is tried, so the object is found bare, fenced, or as the whole
    text. Narration may quote an example object, so the last one is the
    agent's own. A truncated object does not decode and is not read, nor
    does one Python cannot decode (nested past the recursion limit, or an
    integer past the digit limit).
    """
    decoder = json.JSONDecoder()
    found: dict[str, Any] | None = None
    start = text.find("{")
    while start != -1:
        try:
            obj, _ = decoder.raw_decode(text, start)
        except (ValueError, RecursionError):  # see embry0.json_text
            obj = None
        if isinstance(obj, dict) and key in obj:
            found = obj
        start = text.find("{", start + 1)
    return found
