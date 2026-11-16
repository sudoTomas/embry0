"""JSONB values read only in part (#527): their type, and the check the readers that act on one make.

The JSONB codec (``embry0.storage.database``) stops reading a value too deep
for ``json.loads`` past a limit; the objects and arrays that cut closes are a
:class:`PartReadDict` or :class:`PartReadList`. A value read in part is marked
so readers refuse it: here, with no dependency, so the agent resolver (which the
sandbox images copy, with ``embry0/safety/``) can make the check too.
"""

from __future__ import annotations

import weakref
from collections.abc import Mapping
from typing import Any

#: Where the codec stopped reading a value longer than its limit: the rest of the value, for a person reading it.
#: Code tells a value read in part by its type (:func:`is_part_read`), never by this text.
TOO_LONG_TO_READ = "[the rest of a document too long to read]"


class PartReadError(ValueError):
    """A JSONB value read only in part (#527) reached code that needs the whole of it."""


class PartReadDict(dict[str, Any]):
    """A JSON object the codec (``embry0.storage.database._loads_deep``) read only in part (#527).

    It holds the keys read before the cut, then :data:`TOO_LONG_TO_READ`: every
    key after it is missing. It is marked so that readers refuse it
    (:func:`refuse_part_read`) instead of taking a missing key for its default,
    and so that it is never written back (the codec's encoder).
    """


class PartReadList(list[Any]):
    """A JSON array the codec (``embry0.storage.database._loads_deep``) read only in part (#527); see :class:`PartReadDict`."""


#: The values read in part still alive in this process, by id. While there is none, the codec's encoder walks
#: nothing. (By id: an object or an array cannot be hashed, so it cannot go in a ``WeakSet``.)
_PART_READS: weakref.WeakValueDictionary[int, Any] = weakref.WeakValueDictionary()


def is_part_read(value: Any) -> bool:
    """Whether ``value`` itself is an object or an array the codec (``embry0.storage.database._loads_deep``) read only in part (#527)."""
    return isinstance(value, (PartReadDict, PartReadList))


def holds_part_read(value: Any) -> bool:
    """Whether ``value`` is, or holds at any depth, a value read only in part (#527). Walks with a stack."""
    stack, seen = [value], set()
    while stack:
        item = stack.pop()
        if is_part_read(item):
            return True
        if isinstance(item, (dict, list, tuple)) and id(item) not in seen:
            seen.add(id(item))
            stack.extend(item.values() if isinstance(item, dict) else item)
    return False


def refuse_part_read(value: Any, what: str) -> None:
    """Raise :class:`PartReadError` when ``value`` was read only in part (#527); ``what`` names it for a person.

    The readers that act on a stored value (settings, a job's configuration,
    an agent's or a profile's) call this before they use it, so a key past the
    cut is never taken for its default. It looks at every depth: a copy of the
    outermost object (``dict(value)``) is no longer marked, but what it holds
    still is.
    """
    if holds_part_read(value):
        raise PartReadError(
            f"{what} is stored nested too deep and too long to read whole, so it was read only in part (#527); "
            "store it again, whole, before it is used"
        )


def refuse_part_read_columns(row: Mapping[str, Any] | None, what: str) -> None:
    """:func:`refuse_part_read` for every column of ``row`` (#527), each named ``<what>.<column>``."""
    for column, value in (row or {}).items():
        refuse_part_read(value, f"{what}.{column}")


def mark_part_read(container: dict[str, Any] | list[Any]) -> PartReadDict | PartReadList:
    """``container``, as read in part (#527): a marked copy, kept track of while it is alive."""
    part: PartReadDict | PartReadList = (
        PartReadDict(container) if isinstance(container, dict) else PartReadList(container)
    )
    _PART_READS[id(part)] = part
    return part


def any_part_read_alive() -> bool:
    """Whether any value read in part is alive in this process."""
    return bool(_PART_READS)
