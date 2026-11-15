"""Telegram webhook callback handler."""

from __future__ import annotations

import hmac
from typing import Any

import structlog
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse, Response

from embry0.api.deps import get_inputs_repo, get_issue_executor
from embry0.notifications.telegram import edit_message_answered
from embry0.services.answer_recorder import record_answer

logger = structlog.get_logger(__name__)
router = APIRouter()


def _as_dict(value: Any) -> dict[str, Any]:
    """Coerce an untrusted payload member to a dict; anything else is empty."""
    return value if isinstance(value, dict) else {}


def _update_origin(update: dict[str, Any]) -> tuple[Any, Any]:
    """Return ``(chat_id, sender_id)`` for the update; either may be None.

    Covers both handled update shapes: a plain ``message`` and a
    ``callback_query``, whose originating chat lives on its attached message.
    """
    cq = _as_dict(update.get("callback_query"))
    if cq:
        message, sender = _as_dict(cq.get("message")), _as_dict(cq.get("from"))
    else:
        message = _as_dict(update.get("message"))
        sender = _as_dict(message.get("from"))
    return _as_dict(message.get("chat")).get("id"), sender.get("id")


@router.post("/telegram/callback", response_model=None)
async def telegram_callback(request: Request) -> Response | dict[str, Any]:
    """Handle incoming Telegram update events.

    Supports two update types:
    1. Reply-to-message: matches the replied-to message_id to a pending input,
       answers it, optionally edits the Telegram message, and resumes the
       pipeline when all blocking inputs are answered.
    2. Callback query (inline button click): acknowledges via answerCallbackQuery.

    Both are scoped to ``telegram_chat_id`` (and optionally
    ``telegram_allowed_sender_ids``) before any lookup — see the gate below.
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
    if not isinstance(update, dict):
        raise HTTPException(status_code=400, detail="Invalid JSON payload")

    config = request.app.state.config

    # -----------------------------------------------------------------------
    # Chat + sender scoping (#247)
    # -----------------------------------------------------------------------
    # The secret token proves the update came from Telegram, not that it came
    # from the operator. ``message_id`` values are per chat, so without this
    # gate any account that can DM the bot could reply with a message_id that
    # collides with a pending question and have it recorded as the answer.
    # Rejections are silent 204s: Telegram retries non-2xx deliveries, and a
    # hostile chat must not learn whether its guess landed.
    chat_id, sender_id = _update_origin(update)
    allowed_chat = str(config.telegram_chat_id or "").strip()
    reject_reason: str | None = None
    if not allowed_chat:
        # Fail closed. An unset chat id means the inbound path is not
        # configured, never "accept every chat".
        reject_reason = "chat_id_unconfigured"
    elif chat_id is None:
        reject_reason = "chat_id_missing"
    elif str(chat_id).strip() != allowed_chat:
        reject_reason = "chat_id_mismatch"
    else:
        allowlist = config.telegram_sender_allowlist
        if allowlist is not None and sender_id not in allowlist.ids:
            reject_reason = "sender_not_allowed"
    if reject_reason is not None:
        allowlist = config.telegram_sender_allowlist
        logger.warning(
            "telegram_update_rejected",
            reason=reject_reason,
            chat_id=chat_id,
            sender_id=sender_id,
            update_id=update.get("update_id"),
            # A non-empty tuple here means TELEGRAM_ALLOWED_SENDER_IDS holds
            # entries that are not ids (e.g. usernames) and admits nobody.
            invalid_allowlist_entries=allowlist.invalid if allowlist is not None else (),
        )
        return Response(status_code=204)

    inputs_repo = get_inputs_repo(request)
    executor = get_issue_executor(request)

    # -----------------------------------------------------------------------
    # Handle inline button callback queries
    # -----------------------------------------------------------------------
    cq = _as_dict(update.get("callback_query"))
    if cq:
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
    message = _as_dict(update.get("message"))
    reply_to = _as_dict(message.get("reply_to_message"))
    if not reply_to:
        logger.debug("telegram_update_ignored", reason="no_reply_to_message")
        return {"status": "ignored"}

    replied_message_id = reply_to.get("message_id")
    if not isinstance(replied_message_id, int):
        return {"status": "ignored"}

    # Look up the input linked to this Telegram message
    inp = await inputs_repo.get_by_telegram_message(replied_message_id)
    if inp is None:
        logger.info("telegram_reply_no_match", replied_message_id=replied_message_id)
        return {"status": "no_match"}

    # RAV-1059: auto_answered stays answerable — a reply to the question
    # message is an explicit human override of the orchestrator's auto-answer
    # (same rule as the dashboard answer endpoints).
    if inp["status"] == "answered":
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
    if username:
        answered_by = f"telegram:{username}"
    elif chat_id is not None:
        answered_by = f"telegram:{chat_id}"
    else:
        answered_by = "telegram"

    # Record the answer via the shared path (RAV-1076: persists + posts the
    # Q&A on the linked Linear issue; the recorder skips the cross-channel
    # Telegram edit for channel="telegram" — this route edits its own message
    # below). The lookup above (``get_by_telegram_message``) does not branch
    # on ``asking_node``: a reply-to-message routes to whichever input row
    # owns that ``telegram_message_id`` — triage ``needs_info`` and
    # developer/review ``agent_ask_user`` rows alike.
    recorded = await record_answer(
        inp=inp,
        answer=answer_text,
        answered_by=answered_by,
        channel="telegram",
        inputs_repo=inputs_repo,
        issues_repo=getattr(request.app.state, "issues_repo", None),
        tracker_channels=getattr(request.app.state, "tracker_channels", None),
        config=config,
    )
    if recorded is False:
        # #499: another answer to this question was taken first, between the read above and this write.
        logger.info("telegram_reply_already_answered", input_id=inp["id"])
        return {"status": "already_answered"}
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

    # Resume pipeline if all blocking inputs are now answered. We delegate to
    # ``executor.resume_for_issue`` — the same entry point used by the GitHub
    # issue_comment inbound path — so cross-channel races stay consistent and
    # the idempotency guard lives in one place.
    pending = await inputs_repo.count_pending_blocking(inp["issue_id"])
    if pending == 0:
        await executor.resume_for_issue(inp["issue_id"])

    return {"status": "answered", "input_id": inp["id"]}
