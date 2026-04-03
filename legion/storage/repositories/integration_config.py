"""Integration config repository — webhook, Slack, and Telegram settings."""

import json
from typing import Any

import structlog

from legion.storage.database import DatabasePool

logger = structlog.get_logger(__name__)

_DEFAULTS: dict[str, Any] = {
    "trigger_labels": ["Legion"],
    "webhook_secret_set": False,
    "slack_webhook_url_set": False,
    "slack_webhook_url_masked": "",
    "telegram_bot_token_set": False,
    "telegram_bot_token_masked": "",
    "telegram_chat_id": "",
}

_UPDATABLE_FIELDS = frozenset(
    {"trigger_labels", "webhook_secret", "slack_webhook_url", "telegram_bot_token", "telegram_chat_id"}
)


def _mask(value: str, visible: int = 4) -> str:
    """Mask a secret string, showing only the last `visible` characters."""
    if not value:
        return ""
    if len(value) <= visible:
        return "*" * len(value)
    return "..." + value[-visible:]


class IntegrationConfigRepository:
    """CRUD for integration configuration (webhooks, Slack, Telegram)."""

    def __init__(self, db: DatabasePool) -> None:
        self._db = db

    async def get(self) -> dict[str, Any]:
        """Get current integration config. Secrets are never returned raw."""
        row = await self._db.fetchrow("SELECT * FROM integration_config WHERE id = 'default'")
        if not row:
            return {**_DEFAULTS}

        trigger_labels = row["trigger_labels"]
        if isinstance(trigger_labels, str):
            trigger_labels = json.loads(trigger_labels)

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
            if key == "trigger_labels":
                value = json.dumps(value)
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
