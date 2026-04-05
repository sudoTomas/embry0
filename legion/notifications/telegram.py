"""Telegram Bot API integration for sending and editing messages."""

from __future__ import annotations

import re

import httpx
import structlog

logger = structlog.get_logger(__name__)

_TELEGRAM_API = "https://api.telegram.org"

# Characters that must be escaped in MarkdownV2
_MDV2_ESCAPE = re.compile(r"([_*\[\]()~`>#+=|{}.!\\-])")


def _escape_mdv2(text: str) -> str:
    """Escape special characters for Telegram MarkdownV2."""
    return _MDV2_ESCAPE.sub(r"\\\1", text)


async def send_question(
    bot_token: str,
    chat_id: str,
    issue_id: str,
    input_id: str,
    question: str,
    asking_node: str,
    repo: str,
    dashboard_url: str,
) -> int | None:
    """Send a blocking question to Telegram and return the message_id.

    Tries MarkdownV2 formatting first; falls back to plain text on parse error.
    Includes an "Answer in Dashboard" inline keyboard button.
    Returns the Telegram message_id or None on failure.
    """
    # Build deep-link URL to the specific input in the dashboard
    answer_url = f"{dashboard_url.rstrip('/')}/issues/{issue_id}/inputs/{input_id}"

    inline_keyboard = {"inline_keyboard": [[{"text": "Answer in Dashboard", "url": answer_url}]]}

    # MarkdownV2 attempt
    repo_tag = f"\\[{_escape_mdv2(repo)}\\]" if repo else ""
    node_tag = _escape_mdv2(asking_node)
    q_escaped = _escape_mdv2(question)
    mdv2_text = f"*Legion* {repo_tag}\n_{node_tag}_ is asking:\n\n{q_escaped}\n\n`input:{_escape_mdv2(input_id)}`"

    payload_mdv2 = {
        "chat_id": chat_id,
        "text": mdv2_text,
        "parse_mode": "MarkdownV2",
        "reply_markup": inline_keyboard,
    }

    async with httpx.AsyncClient(timeout=15.0) as client:
        resp = await client.post(
            f"{_TELEGRAM_API}/bot{bot_token}/sendMessage",
            json=payload_mdv2,
        )

        if resp.status_code == 200 and resp.json().get("ok"):
            message_id: int = resp.json()["result"]["message_id"]
            logger.info("telegram_question_sent", input_id=input_id, message_id=message_id)
            return message_id

        # Fall back to plain text
        logger.warning(
            "telegram_mdv2_failed",
            status=resp.status_code,
            body=resp.text[:200],
            input_id=input_id,
        )
        repo_prefix = f"[{repo}] " if repo else ""
        plain_text = f"Legion {repo_prefix}\n{asking_node} is asking:\n\n{question}\n\ninput:{input_id}"
        payload_plain = {
            "chat_id": chat_id,
            "text": plain_text,
            "reply_markup": inline_keyboard,
        }
        resp2 = await client.post(
            f"{_TELEGRAM_API}/bot{bot_token}/sendMessage",
            json=payload_plain,
        )
        if resp2.status_code == 200 and resp2.json().get("ok"):
            message_id = resp2.json()["result"]["message_id"]
            logger.info("telegram_question_sent_plain", input_id=input_id, message_id=message_id)
            return message_id

        logger.error(
            "telegram_send_failed",
            status=resp2.status_code,
            body=resp2.text[:200],
            input_id=input_id,
        )
        return None


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
