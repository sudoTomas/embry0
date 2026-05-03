"""Test the channel fan-out — each channel in issue.notification_channels
should be invoked exactly once per dispatch."""

from unittest.mock import AsyncMock, MagicMock

import pytest


@pytest.mark.asyncio
async def test_dispatch_to_dashboard_only_invokes_no_external_channel():
    """When channels=['dashboard'], no Telegram or GitHub-comment is invoked."""
    from athanor.notifications.channels import DashboardChannel, dispatch_to_channels

    dashboard = DashboardChannel()
    dashboard.dispatch = AsyncMock()
    telegram = MagicMock()
    telegram.dispatch = AsyncMock()

    await dispatch_to_channels(
        channels=["dashboard"],
        registry={"dashboard": dashboard, "telegram": telegram},
        issue={"id": "iss-x", "github_number": None},
        questions=[{"question": "Q?", "input_id": "inp-1"}],
    )
    dashboard.dispatch.assert_awaited_once()
    telegram.dispatch.assert_not_awaited()


@pytest.mark.asyncio
async def test_dispatch_to_dashboard_and_telegram_invokes_both():
    from athanor.notifications.channels import dispatch_to_channels

    dashboard = MagicMock()
    dashboard.dispatch = AsyncMock()
    telegram = MagicMock()
    telegram.dispatch = AsyncMock()

    await dispatch_to_channels(
        channels=["dashboard", "telegram"],
        registry={"dashboard": dashboard, "telegram": telegram},
        issue={"id": "iss-x", "github_number": None},
        questions=[{"question": "Q?", "input_id": "inp-1"}],
    )
    dashboard.dispatch.assert_awaited_once()
    telegram.dispatch.assert_awaited_once()


@pytest.mark.asyncio
async def test_dispatch_skips_unknown_channel_with_warning():
    """An unknown channel name in issue.notification_channels does not crash —
    it logs a warning and continues with the rest."""
    from athanor.notifications.channels import dispatch_to_channels

    dashboard = MagicMock()
    dashboard.dispatch = AsyncMock()

    # Should not raise; should still dispatch to dashboard
    await dispatch_to_channels(
        channels=["dashboard", "nonexistent-channel"],
        registry={"dashboard": dashboard},
        issue={"id": "iss-x", "github_number": None},
        questions=[{"question": "Q?", "input_id": "inp-1"}],
    )
    dashboard.dispatch.assert_awaited_once()


@pytest.mark.asyncio
async def test_one_channel_failure_does_not_block_others():
    """If Telegram dispatch raises, dashboard still gets dispatched."""
    from athanor.notifications.channels import dispatch_to_channels

    dashboard = MagicMock()
    dashboard.dispatch = AsyncMock()
    telegram = MagicMock()
    telegram.dispatch = AsyncMock(side_effect=RuntimeError("Telegram down"))

    # Should not raise
    await dispatch_to_channels(
        channels=["dashboard", "telegram"],
        registry={"dashboard": dashboard, "telegram": telegram},
        issue={"id": "iss-x", "github_number": None},
        questions=[{"question": "Q?", "input_id": "inp-1"}],
    )
    dashboard.dispatch.assert_awaited_once()
    telegram.dispatch.assert_awaited_once()


@pytest.mark.asyncio
async def test_dispatch_runs_channels_concurrently():
    """Two channels each take ~50ms; total wall-clock should be ~50ms (gather),
    not ~100ms (sequential)."""
    import asyncio
    import time

    from athanor.notifications.channels import dispatch_to_channels

    async def slow_dispatch(_issue, _questions):
        await asyncio.sleep(0.05)

    a = MagicMock()
    a.dispatch = slow_dispatch
    b = MagicMock()
    b.dispatch = slow_dispatch

    start = time.monotonic()
    await dispatch_to_channels(
        channels=["a", "b"],
        registry={"a": a, "b": b},
        issue={"id": "iss-1", "github_number": None},
        questions=[{"question": "Q?", "input_id": "inp-1"}],
    )
    elapsed = time.monotonic() - start
    # Sequential would be ~100ms; concurrent ~50ms. Allow generous slack.
    assert elapsed < 0.085, f"expected concurrent dispatch (<0.085s); got {elapsed:.3f}s"
