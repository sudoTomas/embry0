"""Notification dispatcher — routes questions and answers to configured channels.

Fan-out across N channels driven by ``issue.notification_channels``. Each
channel is a small object that conforms to the
:class:`embry0.notifications.channels.NotificationChannel` protocol. Channels
are injected by the caller (typically ``IssueExecutor`` from ``app.state``) so
unit tests can mock them.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import structlog

from embry0.notifications.channels import DashboardChannel, dispatch_to_channels
from embry0.notifications.telegram import edit_message_answered

if TYPE_CHECKING:
    from embry0.config import Embry0Config

logger = structlog.get_logger(__name__)


async def dispatch_questions(
    questions: list[dict[str, Any]],
    issue: dict[str, Any],
    *,
    github_comment_channel: Any | None = None,
) -> None:
    """Fan dispatch out to every channel in ``issue.notification_channels``.

    The dashboard channel is always present (as a no-op — the dashboard reads
    from ``issue_inputs`` directly). The GitHub-comment channel is optional
    and must be injected by the caller; if not injected, it is simply absent
    from the registry and a request for it logs a warning.

    Args:
        questions: List of question dicts. Each must contain at least
            ``question`` and ``input_id``; channels may consume additional
            keys (``options``, ``asking_node``, ``importance``, ...).
        issue: Issue dict with at least ``id``; channels may consume
            ``repo``, ``github_number``, ``title``,
            ``notification_channels``.
        github_comment_channel: Optional GitHub-comment channel object
            conforming to the :class:`NotificationChannel` protocol.
    """
    requested = issue.get("notification_channels") or ["dashboard"]
    registry: dict[str, Any] = {"dashboard": DashboardChannel()}
    if github_comment_channel is not None:
        registry["github"] = github_comment_channel
    await dispatch_to_channels(channels=requested, registry=registry, issue=issue, questions=questions)


async def notify_answer_cross_channel(
    inputs_repo: Any,
    inp: dict[str, Any],
    answer: str,
    answered_by: str,
    config: Embry0Config,
) -> None:
    """Propagate an answer to channels that were not the source of the answer.

    Currently: if the answer did NOT come via Telegram, edit the Telegram message
    to reflect the answered state.
    """
    bot_token: str = getattr(config, "telegram_bot_token", "")
    chat_id: str = getattr(config, "telegram_chat_id", "")

    if not bot_token or not chat_id:
        return

    telegram_message_id: int | None = inp.get("telegram_message_id")
    if telegram_message_id is None:
        return

    # Only edit if this answer came from somewhere other than Telegram
    if answered_by == "telegram":
        return

    try:
        await edit_message_answered(
            bot_token=bot_token,
            chat_id=chat_id,
            message_id=telegram_message_id,
            answer=answer,
        )
    except Exception:
        logger.warning(
            "cross_channel_telegram_edit_failed",
            input_id=inp.get("id"),
            exc_info=True,
        )
