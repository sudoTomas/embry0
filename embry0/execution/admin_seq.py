"""The numbers the orchestrator gives the admin requests it sends the proxies.

A proxy takes a request for a sandbox or a credential only when its number is newer than the last it took for the
same one (:class:`embry0.execution.proxy.enrollment.AdminSequence`). A request is numbered when it is decided, not
when it is sent: a run's end when the run ends, a token push when the token is fetched. One counter serves the whole
process, so every number taken here is ordered with every other.

The counter starts at the wall clock, so a later process's numbers are newer than an earlier one's. A clock that goes
back across a restart cannot lock a proxy out: ``ProxyManager.start()`` relaunches every proxy, so a proxy never
compares a number of this process with one of the last. A proxy kept across restarts would need this counter seeded
from the numbers the proxies last took.
"""

from __future__ import annotations

import time

_last = 0


def next_admin_seq() -> int:
    """A number newer than every one this process gave before (and, while the clock does not go back, than an earlier
    process's)."""
    global _last
    _last = max(time.time_ns(), _last + 1)
    return _last
