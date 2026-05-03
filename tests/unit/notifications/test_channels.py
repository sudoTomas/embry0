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
