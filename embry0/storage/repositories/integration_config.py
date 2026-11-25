"""Integration config repository — webhook, Slack, and Telegram settings.

Defaults are derived from environment variables on every read, so changes
to ``.env`` (TRIGGER_LABELS, GITHUB_WEBHOOK_SECRET, SLACK_WEBHOOK_URL,
TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID) flow through to the Settings UI on a
fresh DB. Once the user saves changes via the UI a row exists in
``integration_config`` and that row takes precedence forever.
"""

import os
from typing import Any, Literal, NamedTuple

import structlog

from embry0.storage.database import DatabasePool

logger = structlog.get_logger(__name__)

_HARDCODED_TRIGGER_LABELS = ["embry0"]

_UPDATABLE_FIELDS = frozenset(
    {
        "trigger_labels",
        "webhook_secret",
        "slack_webhook_url",
        "telegram_bot_token",
        "telegram_chat_id",
        "webhook_notify_url",
        "webhook_notify_secret",
        "linear_api_key",
        "linear_webhook_secret",
        "linear_repo_map",
    }
)


_SECRET_FIELDS = {
    "webhook_secret",
    "slack_webhook_url",
    "telegram_bot_token",
    "webhook_notify_secret",
    "linear_api_key",
    "linear_webhook_secret",
}


def _mask(value: str, visible: int = 4) -> str:
    """Mask a secret string, showing only the last `visible` characters."""
    if not value:
        return ""
    if len(value) <= visible:
        return "*" * len(value)
    return "..." + value[-visible:]


class ResolvedTriggerLabels(NamedTuple):
    """The effective trigger labels and which layer supplied them (#270)."""

    labels: list[str]
    source: Literal["db", "env", "default"]


def _env_trigger_labels() -> list[str]:
    """TRIGGER_LABELS as a list; empty when unset, blank, or all separators."""
    raw = os.environ.get("TRIGGER_LABELS") or ""
    return [lbl.strip() for lbl in raw.split(",") if lbl.strip()]


def _fallback_trigger_labels() -> ResolvedTriggerLabels:
    """What applies when the DB row has no labels: env, else the code default.

    An env value that parses to NO labels (``TRIGGER_LABELS=","``) falls
    through to the default like an unset one — an empty set silently stops
    every dispatch, which no operator means by a stray comma.
    """
    env_labels = _env_trigger_labels()
    if env_labels:
        return ResolvedTriggerLabels(env_labels, "env")
    return ResolvedTriggerLabels(list(_HARDCODED_TRIGGER_LABELS), "default")


def resolved_trigger_labels(stored: Any) -> ResolvedTriggerLabels:
    """The effective trigger labels from the row's stored ``trigger_labels``: those, else env, else the default."""
    if stored:
        return ResolvedTriggerLabels([str(label) for label in stored], "db")
    return _fallback_trigger_labels()


def _env_derived_defaults() -> dict[str, Any]:
    """Read defaults from env at call time. Returns the same masked-secret shape as get()."""
    webhook_secret = os.environ.get("GITHUB_WEBHOOK_SECRET", "")
    slack_url = os.environ.get("SLACK_WEBHOOK_URL", "")
    telegram_token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
    notify_secret = os.environ.get("WEBHOOK_NOTIFY_SECRET", "")
    linear_key = os.environ.get("LINEAR_API_KEY", "")
    linear_secret = os.environ.get("LINEAR_WEBHOOK_SECRET", "")
    return {
        "trigger_labels": _fallback_trigger_labels().labels,
        "webhook_secret_set": bool(webhook_secret),
        "slack_webhook_url_set": bool(slack_url),
        "slack_webhook_url_masked": _mask(slack_url),
        "telegram_bot_token_set": bool(telegram_token),
        "telegram_bot_token_masked": _mask(telegram_token),
        "telegram_chat_id": os.environ.get("TELEGRAM_CHAT_ID", ""),
        "webhook_notify_url": os.environ.get("WEBHOOK_NOTIFY_URL", ""),
        "webhook_notify_secret_set": bool(notify_secret),
        "webhook_notify_secret_masked": _mask(notify_secret),
        "linear_api_key_set": bool(linear_key),
        "linear_api_key_masked": _mask(linear_key),
        "linear_webhook_secret_set": bool(linear_secret),
        "linear_webhook_secret_masked": _mask(linear_secret),
        "linear_repo_map": os.environ.get("LINEAR_REPO_MAP", ""),
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
        row_map = dict(row)
        webhook_notify_secret: str = row_map.get("webhook_notify_secret") or ""
        # Empty column falls back to env, mirroring the raw getters below —
        # otherwise the Settings UI would report Linear as unconfigured on a
        # deployment that configures it purely through LINEAR_* env vars.
        linear_api_key: str = (row_map.get("linear_api_key") or "") or os.environ.get("LINEAR_API_KEY", "")
        linear_webhook_secret: str = (row_map.get("linear_webhook_secret") or "") or os.environ.get(
            "LINEAR_WEBHOOK_SECRET", ""
        )
        linear_repo_map: str = (row_map.get("linear_repo_map") or "") or os.environ.get("LINEAR_REPO_MAP", "")

        return {
            "trigger_labels": trigger_labels,
            "webhook_secret_set": bool(webhook_secret),
            "slack_webhook_url_set": bool(slack_webhook_url),
            "slack_webhook_url_masked": _mask(slack_webhook_url),
            "telegram_bot_token_set": bool(telegram_bot_token),
            "telegram_bot_token_masked": _mask(telegram_bot_token),
            "telegram_chat_id": row["telegram_chat_id"] or "",
            "webhook_notify_url": row_map.get("webhook_notify_url") or "",
            "webhook_notify_secret_set": bool(webhook_notify_secret),
            "webhook_notify_secret_masked": _mask(webhook_notify_secret),
            "linear_api_key_set": bool(linear_api_key),
            "linear_api_key_masked": _mask(linear_api_key),
            "linear_webhook_secret_set": bool(linear_webhook_secret),
            "linear_webhook_secret_masked": _mask(linear_webhook_secret),
            "linear_repo_map": linear_repo_map,
        }

    async def get_trigger_labels(self) -> list[str]:
        """Effective trigger labels for every forge's webhook path.

        Same contract as the credential getters below: DB row wins, env fills
        the gap. This is the single source of truth — before it existed the
        webhook read ``config.trigger_labels_list`` directly, so the
        dashboard-editable column was write-only and Linear used a private
        hardcoded constant.

        A row whose ``trigger_labels`` is NULL or empty falls back to env
        rather than disabling the trigger outright: an empty set silently
        stops every job from dispatching, which is never what an operator
        meant by saving an unrelated Settings field.
        """
        return (await self.resolve_trigger_labels()).labels

    async def resolve_trigger_labels(self) -> ResolvedTriggerLabels:
        """:meth:`get_trigger_labels` plus the layer that won (#270).

        The source is what makes a DB/.env disagreement diagnosable: the two
        drifted apart on the live deploy and nothing said which one dispatch
        was actually matching against.
        """
        row = await self._db.fetchrow("SELECT trigger_labels FROM integration_config WHERE id = 'default'")
        return resolved_trigger_labels(row["trigger_labels"] if row else None)

    async def log_effective_trigger_labels(self) -> None:
        """Startup line: the label dispatch matches on, and where it came from.

        Also warns when TRIGGER_LABELS is set but shadowed by a different DB
        value — the env value is then dead config that reads as authoritative.
        Advisory only: never fails startup.
        """
        try:
            resolved = await self.resolve_trigger_labels()
        except Exception:
            logger.warning("trigger_labels_resolve_failed", exc_info=True)
            return
        logger.info("trigger_labels_effective", labels=resolved.labels, source=resolved.source)
        env_labels = _env_trigger_labels()
        env_value = os.environ.get("TRIGGER_LABELS") or ""
        if env_value.strip() and not env_labels:
            logger.warning(
                "trigger_labels_env_empty",
                env_value=env_value,
                msg="TRIGGER_LABELS is set but contains no label; it is ignored rather than "
                "disabling every trigger. Set a label or remove the variable.",
            )
        if resolved.source == "db" and env_labels and env_labels != resolved.labels:
            logger.warning(
                "trigger_labels_env_shadowed",
                env_labels=env_labels,
                effective_labels=resolved.labels,
                msg="TRIGGER_LABELS in .env is ignored: the integration_config DB row wins. "
                "Align .env with the DB value, or change the label in Settings → Integrations.",
            )

    async def get_linear_credentials(self) -> tuple[str, str, str]:
        """RAW (api_key, webhook_secret, repo_map) — never surface via API.

        Same contract as the sibling credential getters (the RAV-657 lesson:
        internal consumers must not read the masked ``get()``). DB row wins;
        empty fields fall back to the LINEAR_* env vars, so a deployment can
        configure Linear either way.
        """
        row = await self._db.fetchrow(
            "SELECT linear_api_key, linear_webhook_secret, linear_repo_map FROM integration_config WHERE id = 'default'"
        )
        row_map = dict(row) if row else {}
        api_key = (row_map.get("linear_api_key") or "") or os.environ.get("LINEAR_API_KEY", "")
        secret = (row_map.get("linear_webhook_secret") or "") or os.environ.get("LINEAR_WEBHOOK_SECRET", "")
        repo_map = (row_map.get("linear_repo_map") or "") or os.environ.get("LINEAR_REPO_MAP", "")
        return api_key, secret, repo_map

    async def get_telegram_credentials(self) -> tuple[str, str]:
        """RAW (bot_token, chat_id) for internal senders — never surface via API.

        RAV-657 hotfix: ``get()`` deliberately masks secrets, so the watcher's
        ping path read an empty token and silently skipped every ping. DB row
        wins; empty fields fall back to the TELEGRAM_* env vars (the same
        source the question dispatcher uses), so either configuration surface
        works.
        """
        row = await self._db.fetchrow(
            "SELECT telegram_bot_token, telegram_chat_id FROM integration_config WHERE id = 'default'"
        )
        token = (row["telegram_bot_token"] if row else "") or os.environ.get("TELEGRAM_BOT_TOKEN", "")
        chat_id = (row["telegram_chat_id"] if row else "") or os.environ.get("TELEGRAM_CHAT_ID", "")
        return token, chat_id

    async def get_webhook_credentials(self) -> tuple[str, str]:
        """RAW (notify_url, notify_secret) for the outbound WebhookNotifier —
        never surface via API.

        Same contract as ``get_telegram_credentials`` (the RAV-657 lesson:
        internal senders must not read the masked ``get()``). DB row wins;
        empty fields fall back to the WEBHOOK_NOTIFY_* env vars, so either
        configuration surface works.
        """
        row = await self._db.fetchrow(
            "SELECT webhook_notify_url, webhook_notify_secret FROM integration_config WHERE id = 'default'"
        )
        row_map = dict(row) if row else {}
        url = (row_map.get("webhook_notify_url") or "") or os.environ.get("WEBHOOK_NOTIFY_URL", "")
        secret = (row_map.get("webhook_notify_secret") or "") or os.environ.get("WEBHOOK_NOTIFY_SECRET", "")
        return url, secret

    async def get_slack_webhook_url(self) -> str:
        """RAW Slack incoming-webhook URL for SlackChannel — never surface via API.

        Same contract as ``get_webhook_credentials`` (the RAV-657 lesson:
        internal senders must not read the masked ``get()``). DB row wins; an
        empty column falls back to ``SLACK_WEBHOOK_URL``, so a settings save
        and the env var both apply without a process restart.
        """
        row = await self._db.fetchrow("SELECT slack_webhook_url FROM integration_config WHERE id = 'default'")
        stored = (row["slack_webhook_url"] if row else "") or ""
        return stored or os.environ.get("SLACK_WEBHOOK_URL", "")

    async def update(self, **fields: Any) -> dict[str, Any]:
        """Upsert integration config fields. Returns updated config via get()."""
        valid = {k: v for k, v in fields.items() if k in _UPDATABLE_FIELDS}
        if not valid:
            return await self.get()

        # Seed trigger_labels from env on first write rather than letting the
        # column DEFAULT ('["embry0"]') apply. The row is created the moment
        # any field is saved — including an unrelated one like the Telegram
        # chat id — and from then on the row wins. Taking the hardcoded column
        # default here would silently discard the operator's TRIGGER_LABELS
        # env value the first time they touched Settings.
        await self._db.execute(
            "INSERT INTO integration_config (id, trigger_labels) VALUES ('default', $1) ON CONFLICT (id) DO NOTHING",
            _fallback_trigger_labels().labels,
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
