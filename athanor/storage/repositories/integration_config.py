"""Integration config repository — webhook, Slack, and Telegram settings.

Defaults are derived from environment variables on every read, so changes
to ``.env`` (TRIGGER_LABELS, GITHUB_WEBHOOK_SECRET, SLACK_WEBHOOK_URL,
TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID) flow through to the Settings UI on a
fresh DB. Once the user saves changes via the UI a row exists in
``integration_config`` and that row takes precedence forever.
"""

import os
from typing import Any

import structlog

from athanor.storage.database import DatabasePool

logger = structlog.get_logger(__name__)

_HARDCODED_TRIGGER_LABELS = ["Athanor"]

_UPDATABLE_FIELDS = frozenset(
    {"trigger_labels", "webhook_secret", "slack_webhook_url", "telegram_bot_token", "telegram_chat_id"}
)


_SECRET_FIELDS = {"webhook_secret", "slack_webhook_url", "telegram_bot_token"}


def _mask(value: str, visible: int = 4) -> str:
    """Mask a secret string, showing only the last `visible` characters."""
    if not value:
        return ""
    if len(value) <= visible:
        return "*" * len(value)
    return "..." + value[-visible:]


def _env_trigger_labels(fallback: list[str]) -> list[str]:
    raw = os.environ.get("TRIGGER_LABELS")
    if raw is None or raw.strip() == "":
        return list(fallback)
    return [lbl.strip() for lbl in raw.split(",") if lbl.strip()]


def _env_derived_defaults() -> dict[str, Any]:
    """Read defaults from env at call time. Returns the same masked-secret shape as get()."""
    webhook_secret = os.environ.get("GITHUB_WEBHOOK_SECRET", "")
    slack_url = os.environ.get("SLACK_WEBHOOK_URL", "")
    telegram_token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
    return {
        "trigger_labels": _env_trigger_labels(_HARDCODED_TRIGGER_LABELS),
        "webhook_secret_set": bool(webhook_secret),
        "slack_webhook_url_set": bool(slack_url),
        "slack_webhook_url_masked": _mask(slack_url),
        "telegram_bot_token_set": bool(telegram_token),
        "telegram_bot_token_masked": _mask(telegram_token),
        "telegram_chat_id": os.environ.get("TELEGRAM_CHAT_ID", ""),
    }


class IntegrationConfigRepository:
    """CRUD for integration configuration (webhooks, Slack, Telegram)."""

    def __init__(self, db: DatabasePool) -> None:
        self._db = db

    async def get(self) -> dict[str, Any]:
        """Get current integration config. Secrets are never returned raw.

        Falls back to env-derived defaults when no row exists.
        """
        row = await self._db.fetchrow("SELECT * FROM integration_config WHERE id = 'default'")
        if not row:
            return _env_derived_defaults()

        trigger_labels = row["trigger_labels"]

        webhook_secret: str = row["webhook_secret"] or ""
        slack_webhook_url: str = row["slack_webhook_url"] or ""
        telegram_bot_token: str = row["telegram_bot_token"] or ""

        return {
            "trigger_labels": trigger_labels,
            "webhook_secret_set": bool(webhook_secret),
            "slack_webhook_url_set": bool(slack_webhook_url),
            "slack_webhook_url_masked": _mask(slack_webhook_url),
            "telegram_bot_token_set": bool(telegram_bot_token),
            "telegram_bot_token_masked": _mask(telegram_bot_token),
            "telegram_chat_id": row["telegram_chat_id"] or "",
        }

    async def update(self, **fields: Any) -> dict[str, Any]:
        """Upsert integration config fields. Returns updated config via get()."""
        valid = {k: v for k, v in fields.items() if k in _UPDATABLE_FIELDS}
        if not valid:
            return await self.get()

        await self._db.execute(
            "INSERT INTO integration_config (id) VALUES ('default') ON CONFLICT (id) DO NOTHING",
        )

        sets: list[str] = []
        args: list[Any] = []
        idx = 1
        for key, value in valid.items():
            if key in _SECRET_FIELDS and value == "":
                continue
            sets.append(f"{key} = ${idx}")
            args.append(value)
            idx += 1
        sets.append("updated_at = NOW()")

        await self._db.execute(
            f"UPDATE integration_config SET {', '.join(sets)} WHERE id = 'default'",
            *args,
        )
        logger.info("integration_config_updated", fields=list(valid.keys()))
        return await self.get()
