"""Read one listing per forge account, all of them at once and within one deadline.

The repository discovery routes (``GET /github/repos``,
``GET /github/discoverable-repos``, ``GET /gitlab/discoverable-projects``)
list what each configured account can see. Read one after another, each
account's request could take its client's whole timeout, page after page, so
a few hanging accounts kept the request open past the time a proxy or tunnel
in front of embry0 waits, and the person got a gateway timeout instead of the
accounts that did answer (#340). :func:`read_each` reads them concurrently, a
few at a time. Each account has a deadline of its own, well short of the
listing's, so an account that hangs frees its slot in time for the ones
waiting behind it; and the listing stops waiting at its own deadline for all
of them. An account not done by either is reported like one that could not be
reached.

With the values below, the first eight accounts each get their full ten
seconds whatever the others do (four hanging accounts listed first free their
slots at ten seconds, and the next four still have fifteen); an account
further down the list gets what is left of the listing's twenty-five.
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
#: How long one account may take, in seconds, its pages included: well short of the listing's deadline, so the
#: accounts waiting for its slot still have time once it gives up.
ACCOUNT_SECONDS = 10.0


class PastDeadline(Exception):
    """An account whose listing had not finished when the deadline came: reported like one that was unreachable."""


async def read_each[T, R](
    entries: Sequence[T],
    read: Callable[[T], Awaitable[R]],
    *,
    concurrency: int | None = None,
    deadline: float | None = None,
    account_deadline: float | None = None,
) -> list[R | BaseException]:
    """``read`` each of ``entries``, at most ``concurrency`` at once, each within ``account_deadline`` seconds of
    its start and all within ``deadline`` seconds.

    Returns, in the order of ``entries``, what each read returned, or the
    exception it raised, or :class:`PastDeadline` for one still running at
    either deadline, which is cancelled. A caller that is itself cancelled
    cancels every read.
    """
    gate = asyncio.Semaphore(concurrency if concurrency is not None else CONCURRENCY)
    own = account_deadline if account_deadline is not None else ACCOUNT_SECONDS

    async def one(entry: T) -> R:
        async with gate:
            try:
                return await asyncio.wait_for(read(entry), timeout=own)
            except TimeoutError:
                raise PastDeadline() from None

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


def nobody_answered(forge: str, refused: Sequence[int], unreachable: int) -> str:
    """The 502's detail when no account of a listing answered: what the forge did, counted by outcome, naming no
    account (the answer's ``failed`` does that). ``refused`` holds the status of each account the forge refused,
    ``unreachable`` the number it never answered for (a transport error, or past a deadline)."""
    if not refused:
        return f"{forge} could not be reached"
    statuses = sorted(set(refused))
    if not unreachable and len(statuses) == 1:
        return f"{forge} API returned {statuses[0]} for every account"
    parts = [f"refused {len(refused)} ({', '.join(str(s) for s in statuses)})"]
    if unreachable:
        parts.append(f"could not be reached for {unreachable}")
    return f"{forge} listed no account: " + " and ".join(parts)
