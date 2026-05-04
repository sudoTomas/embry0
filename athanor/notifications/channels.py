"""Notification channel abstraction.

A channel knows how to dispatch one batch of agent questions to one
specific surface (dashboard, Telegram chat, GitHub issue comment). The
fan-out helper invokes every requested channel concurrently with
isolated try/except so one channel's failure can't block another.
"""

from __future__ import annotations

import asyncio
from typing import Any, Protocol

import structlog

logger = structlog.get_logger(__name__)


class NotificationChannel(Protocol):
    """Each channel has one async dispatch method.

    Args:
      issue: dict with at least {id, repo, github_number, title}.
      questions: list of dicts, each with {question, input_id, options, asking_node}.
    """

    async def dispatch(self, issue: dict[str, Any], questions: list[dict[str, Any]]) -> None: ...


class DashboardChannel:
    """No-op channel — dashboard reads from issue_inputs directly. Included
    for symmetry so dispatch_to_channels can iterate uniformly."""

    async def dispatch(self, issue: dict[str, Any], questions: list[dict[str, Any]]) -> None:
        logger.debug("dashboard_channel_noop", issue_id=issue.get("id"), n=len(questions))


async def dispatch_to_channels(
    channels: list[str],
    registry: dict[str, NotificationChannel],
    issue: dict[str, Any],
    questions: list[dict[str, Any]],
) -> None:
    """Dispatch to every channel in `channels`. Unknown channels are skipped
    with a warning. Per-channel failures are logged and swallowed."""

    async def _safe_dispatch(name: str) -> None:
        ch = registry.get(name)
        if ch is None:
            logger.warning(
                "unknown_notification_channel",
                channel=name,
                issue_id=issue.get("id"),
            )
            return
        try:
            await ch.dispatch(issue, questions)
        except Exception as exc:
            logger.warning(
                "channel_dispatch_failed",
                channel=name,
                issue_id=issue.get("id"),
                error=str(exc),
            )

    await asyncio.gather(*(_safe_dispatch(c) for c in channels), return_exceptions=False)
