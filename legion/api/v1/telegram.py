"""Telegram webhook callback handler."""

from __future__ import annotations

import structlog
from fastapi import APIRouter, HTTPException, Request

from legion.api.deps import get_inputs_repo, get_issue_executor, get_issues_repo
from legion.api.v1.issues import _resume_pipeline
from legion.notifications.telegram import edit_message_answered

logger = structlog.get_logger(__name__)
router = APIRouter()


@router.post("/telegram/callback")
async def telegram_callback(request: Request) -> dict:
    """Handle incoming Telegram update events.

    Supports two update types:
    1. Reply-to-message: matches the replied-to message_id to a pending input,
       answers it, optionally edits the Telegram message, and resumes the
       pipeline when all blocking inputs are answered.
    2. Callback query (inline button click): acknowledges via answerCallbackQuery.
    """
    try:
        update = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON payload")

    config = request.app.state.config
    inputs_repo = get_inputs_repo(request)
    issues_repo = get_issues_repo(request)
    executor = get_issue_executor(request)

    # -----------------------------------------------------------------------
    # Handle inline button callback queries
    # -----------------------------------------------------------------------
    if "callback_query" in update:
        cq = update["callback_query"]
        cq_id = cq.get("id")
        if cq_id and config.telegram_bot_token:
            import httpx
            async with httpx.AsyncClient(timeout=10.0) as client:
                await client.post(
                    f"https://api.telegram.org/bot{config.telegram_bot_token}/answerCallbackQuery",
                    json={"callback_query_id": cq_id},
                )
        logger.info("telegram_callback_query_acked", callback_query_id=cq_id)
        return {"status": "acked"}

    # -----------------------------------------------------------------------
    # Handle text replies to existing messages
    # -----------------------------------------------------------------------
    message = update.get("message", {})
    reply_to = message.get("reply_to_message")
    if reply_to is None:
        logger.debug("telegram_update_ignored", reason="no_reply_to_message")
        return {"status": "ignored"}

    replied_message_id: int = reply_to.get("message_id")
    if replied_message_id is None:
        return {"status": "ignored"}

    # Look up the input linked to this Telegram message
    inp = await inputs_repo.get_by_telegram_message(replied_message_id)
    if inp is None:
        logger.info("telegram_reply_no_match", replied_message_id=replied_message_id)
        return {"status": "no_match"}

    if inp["status"] in ("answered", "auto_answered"):
        logger.info("telegram_reply_already_answered", input_id=inp["id"])
        return {"status": "already_answered"}

    # Extract reply text
    answer_text: str = message.get("text", "").strip()
    if not answer_text:
        return {"status": "ignored", "reason": "empty_reply"}

    # Record the answer
    await inputs_repo.answer(inp["id"], answer=answer_text, answered_by="telegram")
    logger.info("telegram_input_answered", input_id=inp["id"])

    # Edit the original Telegram message to reflect the answer
    if config.telegram_bot_token and config.telegram_chat_id:
        try:
            await edit_message_answered(
                bot_token=config.telegram_bot_token,
                chat_id=config.telegram_chat_id,
                message_id=replied_message_id,
                answer=answer_text,
            )
        except Exception:
            logger.warning("telegram_edit_after_answer_failed", input_id=inp["id"], exc_info=True)

    # Cross-channel sync (edit Telegram message from other channels is a no-op here
    # since we already edited; dispatcher handles other direction)
    from legion.notifications.dispatcher import notify_answer_cross_channel
    try:
        await notify_answer_cross_channel(
            inputs_repo=inputs_repo,
            inp=inp,
            answer=answer_text,
            answered_by="telegram",
            config=config,
        )
    except Exception:
        logger.warning("cross_channel_sync_failed", input_id=inp["id"], exc_info=True)

    # Resume pipeline if all blocking inputs are now answered
    pending = await inputs_repo.count_pending_blocking(inp["issue_id"])
    if pending == 0:
        await _resume_pipeline(inp["issue_id"], issues_repo, inputs_repo, executor)

    return {"status": "answered", "input_id": inp["id"]}
