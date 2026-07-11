import asyncio

import pytest

from embry0.workflows.qa.watchdogs import IdleWatchdog, TotalBudgetWatchdog


@pytest.mark.asyncio
async def test_idle_watchdog_fires_after_grace_period():
    fired = asyncio.Event()

    async def kill():
        fired.set()

    wd = IdleWatchdog(grace_seconds=0.2, on_timeout=kill)
    wd.start()
    try:
        await asyncio.wait_for(fired.wait(), timeout=1.0)
    finally:
        wd.stop()


@pytest.mark.asyncio
async def test_idle_watchdog_does_not_fire_with_heartbeats():
    fired = asyncio.Event()

    async def kill():
        fired.set()

    wd = IdleWatchdog(grace_seconds=0.2, on_timeout=kill)
    wd.start()
    try:
        for _ in range(5):
            wd.heartbeat()
            await asyncio.sleep(0.05)
        assert not fired.is_set()
    finally:
        wd.stop()


@pytest.mark.asyncio
async def test_total_budget_watchdog_fires_at_budget():
    fired = asyncio.Event()

    async def kill():
        fired.set()

    wd = TotalBudgetWatchdog(budget_seconds=0.15, on_timeout=kill)
    wd.start()
    try:
        await asyncio.wait_for(fired.wait(), timeout=0.5)
    finally:
        wd.stop()


@pytest.mark.asyncio
async def test_stop_cancels_pending_timer():
    fired = asyncio.Event()

    async def kill():
        fired.set()

    wd = TotalBudgetWatchdog(budget_seconds=2.0, on_timeout=kill)
    wd.start()
    wd.stop()
    await asyncio.sleep(0.1)
    assert not fired.is_set()
