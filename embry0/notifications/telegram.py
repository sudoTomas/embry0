"""Telegram Bot API integration — inbound reply webhook and message editing."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import Any

import httpx
import structlog

from embry0.notifications.retry import (
    PermanentSendError,
    SendError,
    TransientSendError,
    classify_response,
    send_with_retry,
)

logger = structlog.get_logger(__name__)

_TELEGRAM_API = "https://api.telegram.org"


def _json_body(response: httpx.Response) -> dict[str, Any]:
    """The response's JSON object, or ``{}`` when it isn't one."""
    try:
        body = response.json()
    except ValueError:
        return {}
    return body if isinstance(body, dict) else {}


def _flood_delay(body: dict[str, Any]) -> float | None:
    """Telegram's advertised flood-control delay, if the body carries one.

    Bot API flood control is authoritative in ``parameters.retry_after`` of
    the JSON body — on a ``429`` the ``Retry-After`` header may be absent, and
    a ``200`` with ``ok: false`` carries no header at all. Retrying inside the
    advertised window just burns the attempt budget and drops the question.
    Zero means "no delay advertised"; the ceiling is
    :func:`~embry0.notifications.retry.send_with_retry`'s, shared with every
    other delay it sleeps.
    """
    raw = (body.get("parameters") or {}).get("retry_after")
    if raw is None:
        return None
    try:
        seconds = float(raw)
    except (TypeError, ValueError):
        return None
    return seconds if seconds > 0 else None


async def send_message(
    bot_token: str,
    chat_id: str,
    text: str,
    *,
    http_client: httpx.AsyncClient | None = None,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
) -> int | None:
    """Send a plain Telegram message. Best-effort — returns the sent message's
    Telegram ``message_id``, or ``None`` on failure; never raises.

    The message_id is what makes reply-to-answer possible: persisted on the
    pending input (``issue_inputs.telegram_message_id``), it lets the inbound
    webhook (``/api/v1/telegram/callback``) match a human's reply back to the
    question it answers. RAV-657 introduced this sender for the watcher's
    human ping; it previously discarded the message_id, which left the whole
    reply-matching path dead.

    Transient failures (429, 5xx, dropped connections, and anything — of any
    status — advertising flood control in ``parameters.retry_after``) are
    retried per :mod:`embry0.notifications.retry`; a plain 4xx and a plain
    ``200`` carrying ``ok: false`` are not — both repeat identically.
    Exhausting the attempts
    still returns ``None``, because every caller reads that as "not
    delivered" (:class:`~embry0.notifications.channels.TelegramChannel` skips
    the mapping write, the watcher reports no ping).

    Text is sent without parse_mode so agent-derived content can't break on
    Markdown entities. ``http_client`` and ``sleep`` exist for tests; in
    production a client is created and closed per call.
    """
    payload = {"chat_id": chat_id, "text": text[:4000]}
    client = http_client or httpx.AsyncClient(timeout=15.0)

    async def attempt(number: int) -> int | None:
        resp = await client.post(f"{_TELEGRAM_API}/bot{bot_token}/sendMessage", json=payload)
        body = _json_body(resp)
        if resp.status_code != 200 or not body.get("ok"):
            logger.warning("telegram_send_failed", status=resp.status_code, body=resp.text[:200], attempt=number)
            flood = _flood_delay(body)
            if flood is not None:
                raise TransientSendError("telegram_flood_control", retry_after=flood)
            if resp.status_code != 200:
                classify_response(resp)
            raise PermanentSendError("telegram_not_ok")
        message_id = (body.get("result") or {}).get("message_id")
        return int(message_id) if message_id is not None else None

    try:
        # Repeatable on purpose (#613): a message Telegram took but answered 5xx, or whose answer was lost, is
        # sent again and can show twice. The Bot API gives a bot no way to read back what it sent, so the outcome
        # cannot be found out, and a question shown twice is a smaller harm than one a person never sees.
        return await send_with_retry(attempt, channel="telegram", sleep=sleep)
    except SendError:
        return None  # logged by the attempt and by notification_failed
    except Exception as exc:
        logger.warning("telegram_send_error", error=str(exc))
        return None
    finally:
        if http_client is None:
            await client.aclose()


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
    # Only the two update shapes the callback handles. Without this Telegram
    # also delivers edited_message / channel_post / etc., which the chat gate
    # (#247) rejects with a warning the operator can do nothing about.
    payload: dict[str, str | list[str]] = {
        "url": webhook_url,
        "allowed_updates": ["message", "callback_query"],
    }
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
