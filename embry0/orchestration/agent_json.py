"""The JSON object an agent writes at the end of its final text.

Agents narrate, then write their result as a JSON object: bare after the
prose, inside a ```json fence, or as the whole text. The executors join every
assistant message into one output, so the object is rarely the whole text and
``json.loads(output)`` misses it. One reader for all of them.
"""

from __future__ import annotations

from typing import Any

from embry0.json_text import json_objects_in


def last_object_with(text: str, key: str) -> dict[str, Any] | None:
    """The last JSON object in ``text`` that has ``key``, else None.

    Every ``{`` is tried, so the object is found bare, fenced, or as the whole
    text. Narration may quote an example object, so the last one is the
    agent's own: the one that starts last, an object nested in another
    included. A truncated object does not decode and is not read, nor
    does one Python cannot decode (nested past the recursion limit, or an
    integer past the digit limit).

    Read from the end, so the agent's own object is usually among the first
    tried; the scan's cost is bounded (:func:`embry0.json_text.json_objects_in`).
    """
    objects = json_objects_in(text, scanner=f"last_object_with:{key}", from_end=True)
    return next((obj for obj in objects if key in obj), None)
