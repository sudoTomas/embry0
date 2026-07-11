"""Telegram webhook callback handler."""

from __future__ import annotations

import hmac
from typing import Any

import structlog
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse, Response

from embry0.api.deps import get_inputs_repo, get_issue_executor
from embry0.notifications.telegram import edit_message_answered

logger = structlog.get_logger(__name__)
router = APIRouter()


@router.post("/telegram/callback", response_model=None)
async def telegram_callback(request: Request) -> Response | dict[str, Any]:
    """Handle incoming Telegram update events.

    Supports two update types:
    1. Reply-to-message: matches the replied-to message_id to a pending input,
       answers it, optionally edits the Telegram message, and resumes the
       pipeline when all blocking inputs are answered.
    2. Callback query (inline button click): acknowledges via answerCallbackQuery.
    """
    expected_secret = getattr(request.app.state, "telegram_webhook_secret", "")
    if not expected_secret:
        # Fail-closed: if the runtime secret is somehow empty (startup race or
        # misconfig), do NOT accept the request. 503 distinguishes "server is
        # not ready to authenticate" from 401 ("your auth was wrong").
        logger.warning("telegram_callback_unconfigured")
        return JSONResponse(
            {"ok": False, "error": "telegram_callback_unconfigured"},
            status_code=503,
        )
    provided = request.headers.get("X-Telegram-Bot-Api-Secret-Token", "")
    if not hmac.compare_digest(provided, expected_secret):
        return JSONResponse({"ok": False, "error": "unauthorized"}, status_code=401)

    try:
        update = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON payload")

    config = request.app.state.config
    inputs_repo = get_inputs_repo(request)
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

    # Identify the answerer for the audit trail. Telegram replies may come
    # from a user with a username, without one (in which case we fall back
    # to the chat id), or from neither (anonymous channel reply — rare).
    sender = message.get("from") or {}
    username = sender.get("username")
    chat_id = (message.get("chat") or {}).get("id")
    if username:
        answered_by = f"telegram:{username}"
    elif chat_id is not None:
        answered_by = f"telegram:{chat_id}"
    else:
        answered_by = "telegram"

    # Record the answer. The lookup above (``get_by_telegram_message``) does
    # not branch on ``asking_node``: a reply-to-message routes to whichever
    # input row owns that ``telegram_message_id`` — triage ``needs_info`` and
    # developer/review ``agent_ask_user`` rows alike.
    await inputs_repo.answer(inp["id"], answer=answer_text, answered_by=answered_by)
    logger.info("telegram_input_answered", input_id=inp["id"], answered_by=answered_by)

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
    from embry0.notifications.dispatcher import notify_answer_cross_channel

    try:
        await notify_answer_cross_channel(
            inputs_repo=inputs_repo,
            inp=inp,
            answer=answer_text,
            answered_by=answered_by,
            config=config,
        )
    except Exception:
        logger.warning("cross_channel_sync_failed", input_id=inp["id"], exc_info=True)

    # Resume pipeline if all blocking inputs are now answered. We delegate to
    # ``executor.resume_for_issue`` — the same entry point used by the GitHub
    # issue_comment inbound path — so cross-channel races stay consistent and
    # the idempotency guard lives in one place.
    pending = await inputs_repo.count_pending_blocking(inp["issue_id"])
    if pending == 0:
        await executor.resume_for_issue(inp["issue_id"])

    return {"status": "answered", "input_id": inp["id"]}
