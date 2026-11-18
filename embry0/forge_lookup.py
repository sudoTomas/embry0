"""The window a lookup reads after a forge write whose outcome is unknown (#613, #620).

A write answered 5xx, or whose answer was lost in transit, may have been made. The issue sync's create and the
question comment each read the forge once for what they sent before sending it again, and both take their window
from here, so the two cannot drift apart: the lookup waits a moment for the item to show, then counts only an item
made at most :data:`LOOKUP_MARGIN` before the write, by the forge's own clock when its failed answer carried one.
Leaf module: imports nothing of embry0.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from email.utils import parsedate_to_datetime
from typing import Any, Final

import httpx

#: How long before the write an item the lookup finds may have been made: the time the write took at the forge,
#: and, when the reference is the local clock, the local clock and the forge's differing.
LOOKUP_MARGIN: Final = timedelta(minutes=5)
#: The pause before the lookup, so an item the forge made as its answer was lost shows in its list.
LOOKUP_PAUSE_SECONDS: Final = 1.5


def forge_time(value: Any) -> datetime | None:
    """A forge timestamp (``2026-01-01T00:00:00Z``) as an aware datetime; None for anything else, a timestamp
    without a zone included, since it cannot be compared with the window."""
    try:
        when = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return when if when.tzinfo is not None else None


def answered_at(response: httpx.Response | None) -> datetime | None:
    """When the forge sent ``response``, by its own clock (its ``Date`` header); None without a readable one."""
    value = response.headers.get("date") if response is not None else None
    if not value:
        return None
    try:
        when = parsedate_to_datetime(value)
    except (TypeError, ValueError, IndexError):
        return None
    return when if when.tzinfo is not None else when.replace(tzinfo=UTC)


def made_since(started: datetime, answered: datetime | None) -> datetime:
    """The earliest an item the lookup finds may have been made.

    A margin before the failed answer, by the forge's clock, when there was one: what the write made, it made
    before it answered, so a local clock running ahead of the forge's cannot push the item out of the window, nor
    one running behind pull an earlier identical item into it. After a lost answer, a margin before ``started``,
    the local time the write was first sent.
    """
    return (answered if answered is not None else started) - LOOKUP_MARGIN
