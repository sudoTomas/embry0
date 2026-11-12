"""A cache of a bounded number of entries, each kept for a time: what the API keeps of a caller between requests.

A cache keyed by what a caller sends (a bearer's hash) grows with every value
a caller sends, so it holds at most ``max_entries``: when a new entry would
pass that, the least recently used one goes. An entry past its expiry reads as
absent and is dropped when it is read. Each read and write takes constant time.
"""

from __future__ import annotations

from collections import OrderedDict


class TtlLruCache[V]:
    """At most ``max_entries`` entries by key, each read until its expiry (a ``time.monotonic()`` value)."""

    def __init__(self, max_entries: int) -> None:
        if max_entries < 1:
            raise ValueError("max_entries must be at least 1")
        self._max_entries = max_entries
        self._entries: OrderedDict[str, tuple[float, V]] = OrderedDict()

    def get(self, key: str, now: float) -> V | None:
        """The entry under ``key`` while it has not expired at ``now``, else None (an expired one is dropped)."""
        found = self._entries.get(key)
        if found is None:
            return None
        expires, value = found
        if expires <= now:
            del self._entries[key]
            return None
        self._entries.move_to_end(key)
        return value

    def put(self, key: str, value: V, expires: float) -> None:
        """Keep ``value`` under ``key`` until ``expires``, dropping the least recently used entries past the bound."""
        self._entries[key] = (expires, value)
        self._entries.move_to_end(key)
        while len(self._entries) > self._max_entries:
            self._entries.popitem(last=False)

    def pop(self, key: str) -> None:
        self._entries.pop(key, None)

    def clear(self) -> None:
        self._entries.clear()

    def __len__(self) -> int:
        return len(self._entries)
