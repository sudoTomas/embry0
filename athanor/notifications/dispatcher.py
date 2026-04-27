"""Notification dispatcher — routes questions and answers to configured channels."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import structlog

from athanor.notifications.github import post_questions_comment
from athanor.notifications.telegram import edit_message_answered, send_question

if TYPE_CHECKING:
    from athanor.config import AthanorConfig
    from athanor.storage.repositories.issue_inputs import IssueInputsRepository

logger = structlog.get_logger(__name__)


async def dispatch_questions(
    inputs_repo: IssueInputsRepository,
    issue: dict[str, Any],
    job_id: str,
    questions: list[tuple[str, str]],  # list of (input_id, question_text)
    asking_node: str,
    config: AthanorConfig,
) -> None:
    """Send blocking questions to all configured channels.

    For Telegram: one message per question; stores telegram_message_id on each input.
    For GitHub: one comment listing all questions.
    Skips channels whose credentials are not configured.
    """
    issue_id: str = issue["id"]
    repo: str = issue.get("repo") or ""
    github_number: int | None = issue.get("github_number")

    # --- Telegram ---
    bot_token: str = getattr(config, "telegram_bot_token", "")
    chat_id: str = getattr(config, "telegram_chat_id", "")
    dashboard_url: str = getattr(config, "telegram_webhook_url", "") or ""

    if bot_token and chat_id:
        for input_id, question in questions:
            try:
                message_id = await send_question(
                    bot_token=bot_token,
                    chat_id=chat_id,
                    issue_id=issue_id,
                    input_id=input_id,
                    question=question,
                    asking_node=asking_node,
                    repo=repo,
                    dashboard_url=dashboard_url,
                )
                if message_id is not None:
                    await inputs_repo.set_telegram_message_id(input_id, message_id)
            except Exception:
                logger.warning(
                    "dispatch_telegram_failed",
                    input_id=input_id,
                    exc_info=True,
                )
    else:
        logger.debug("dispatch_telegram_skipped", reason="not_configured")

    # --- GitHub ---
    github_token: str = getattr(config, "github_token", "")
    if github_token and repo and github_number:
        question_texts = [q for _, q in questions]
        try:
            await post_questions_comment(
                github_token=github_token,
                repo=repo,
                issue_number=github_number,
                questions=question_texts,
                asking_node=asking_node,
            )
        except Exception:
            logger.warning("dispatch_github_failed", issue_id=issue_id, exc_info=True)
    else:
        logger.debug("dispatch_github_skipped", reason="not_configured_or_no_github_issue")


async def notify_answer_cross_channel(
    inputs_repo: IssueInputsRepository,
    inp: dict[str, Any],
    answer: str,
    answered_by: str,
    config: AthanorConfig,
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
