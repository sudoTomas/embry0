"""Counting the settings snapshots one call takes, while the app's own tickers read the same store.

The real lifespan starts tickers (the ask loop, the scout, the Slop-O-Meter,
the leads finders and more) that each take a snapshot of the settings store
when they tick. A ticker's first tick normally runs while the app starts,
but on a loaded runner it can come at any moment of a test, so a count kept
on the store itself takes in the ticker's read as well (#412).
:func:`snapshots_taken` counts only the snapshots taken from inside its call
and from the tasks that call starts, as :func:`tests.fixtures.statements.counted`
does for statements. A ticker's task was started before the call, so its
reads are never counted or answered.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from contextvars import ContextVar
from dataclasses import dataclass

import pytest

from embry0.settings.store import SettingsStore, Snapshot


@dataclass
class _Taken:
    count: int = 0
    answer: Callable[[int], Snapshot] | None = None


_TAKING: ContextVar[_Taken | None] = ContextVar("taking_snapshots", default=None)


def _watch(store: SettingsStore, monkeypatch: pytest.MonkeyPatch) -> None:
    real = store.snapshot

    async def snapshot() -> Snapshot:
        taken = _TAKING.get()
        if taken is None:
            return await real()
        taken.count += 1
        return taken.answer(taken.count) if taken.answer is not None else await real()

    snapshot.watched = True  # type: ignore[attr-defined]
    monkeypatch.setattr(store, "snapshot", snapshot)


async def snapshots_taken[T](
    store: SettingsStore,
    monkeypatch: pytest.MonkeyPatch,
    call: Awaitable[T],
    *,
    answer: Callable[[int], Snapshot] | None = None,
) -> tuple[T, int]:
    """Await ``call``: its result, and how many snapshots of ``store`` it and the tasks it starts took.

    With ``answer``, the n-th of those snapshots is ``answer(n)`` instead of
    the store's; every other caller still reads the store. ``store`` is
    patched on first use and undone with ``monkeypatch``.
    """
    if not getattr(store.snapshot, "watched", False):
        _watch(store, monkeypatch)
    taken = _Taken(answer=answer)
    token = _TAKING.set(taken)
    try:
        result = await call
    finally:
        _TAKING.reset(token)
    return result, taken.count
