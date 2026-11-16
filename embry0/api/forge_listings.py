"""Read one listing per forge account, all of them at once and within one deadline.

The repository discovery routes (``GET /github/repos``,
``GET /github/discoverable-repos``, ``GET /gitlab/discoverable-projects``)
list what each configured account can see. Read one after another, each
account's request could take its client's whole timeout, page after page, so
a few hanging accounts kept the request open past the time a proxy or tunnel
in front of embry0 waits, and the person got a gateway timeout instead of the
accounts that did answer (#340). :func:`read_each` reads them concurrently, a
few at a time, and stops waiting at one deadline for all of them: an account
not done by then is reported like one that could not be reached.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Sequence

#: How many accounts are read at once: enough that a few slow ones do not hold the rest back, few enough not to
#: look like a burst to the forge.
CONCURRENCY = 4
#: How long a listing waits for every account, in seconds: under the minute a proxy or tunnel in front of embry0
#: commonly waits for an answer.
DEADLINE_SECONDS = 25.0


class PastDeadline(Exception):
    """An account whose listing had not finished when the deadline came: reported like one that was unreachable."""


async def read_each[T, R](
    entries: Sequence[T],
    read: Callable[[T], Awaitable[R]],
    *,
    concurrency: int | None = None,
    deadline: float | None = None,
) -> list[R | BaseException]:
    """``read`` each of ``entries``, at most ``concurrency`` at once, all within ``deadline`` seconds.

    Returns, in the order of ``entries``, what each read returned, or the
    exception it raised, or :class:`PastDeadline` for one still running at the
    deadline, which is cancelled. A caller that is itself cancelled cancels
    every read.
    """
    gate = asyncio.Semaphore(concurrency if concurrency is not None else CONCURRENCY)

    async def one(entry: T) -> R:
        async with gate:
            return await read(entry)

    tasks = [asyncio.create_task(one(entry)) for entry in entries]
    if not tasks:
        return []
    try:
        await asyncio.wait(tasks, timeout=deadline if deadline is not None else DEADLINE_SECONDS)
    finally:
        for task in tasks:
            if not task.done():
                task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
    results: list[R | BaseException] = []
    for task in tasks:
        if task.cancelled():
            results.append(PastDeadline())
        elif (error := task.exception()) is not None:
            results.append(error)
        else:
            results.append(task.result())
    return results
