"""Telegram Bot API integration — inbound reply webhook and message editing."""

from __future__ import annotations

import httpx
import structlog

logger = structlog.get_logger(__name__)

_TELEGRAM_API = "https://api.telegram.org"


async def send_message(bot_token: str, chat_id: str, text: str) -> bool:
    """Send a plain Telegram message. Best-effort — returns False, never raises.

    RAV-657: the watcher's human ping. Text is sent without parse_mode so
    agent-derived content can't break on Markdown entities.
    """
    payload = {"chat_id": chat_id, "text": text[:4000]}
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.post(f"{_TELEGRAM_API}/bot{bot_token}/sendMessage", json=payload)
        ok = resp.status_code == 200 and bool(resp.json().get("ok"))
        if not ok:
            logger.warning("telegram_send_failed", status=resp.status_code, body=resp.text[:200])
        return ok
    except Exception as exc:
        logger.warning("telegram_send_error", error=str(exc))
        return False


async def edit_message_answered(
    bot_token: str,
    chat_id: str,
    message_id: int,
    answer: str,
) -> None:
    """Edit a previously sent Telegram message to show it has been answered."""
    text = f"Answered: {answer}"
    payload = {
        "chat_id": chat_id,
        "message_id": message_id,
        "text": text,
    }
    async with httpx.AsyncClient(timeout=15.0) as client:
        resp = await client.post(
            f"{_TELEGRAM_API}/bot{bot_token}/editMessageText",
            json=payload,
        )
    if resp.status_code == 200 and resp.json().get("ok"):
        logger.info("telegram_message_edited", message_id=message_id)
    else:
        logger.warning(
            "telegram_edit_failed",
            message_id=message_id,
            status=resp.status_code,
            body=resp.text[:200],
        )


async def register_webhook(bot_token: str, webhook_url: str, secret_token: str = "") -> bool:
    """Register a webhook URL with the Telegram Bot API. Returns True on success."""
    payload: dict[str, str] = {"url": webhook_url}
    if secret_token:
        payload["secret_token"] = secret_token
    async with httpx.AsyncClient(timeout=15.0) as client:
        resp = await client.post(
            f"{_TELEGRAM_API}/bot{bot_token}/setWebhook",
            json=payload,
        )
    ok = resp.status_code == 200 and resp.json().get("ok", False)
    if ok:
        logger.info("telegram_webhook_registered", webhook_url=webhook_url)
    else:
        logger.error(
            "telegram_webhook_registration_failed",
            status=resp.status_code,
            body=resp.text[:200],
        )
    return ok
