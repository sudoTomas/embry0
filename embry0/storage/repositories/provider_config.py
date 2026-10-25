"""Provider config repository — model tier settings + credential status.

Defaults are derived from environment variables on every read, so changes
to ``.env`` flow through to the Settings UI on a fresh DB. Once the user
saves changes via the UI a row exists in ``provider_config`` and that row
takes precedence forever.

RAV-1239: the provider_mode/ollama_base_url fields were removed from the
config surface — no execution path ever read them. Their DB columns
remain (ledger migrations stay) but are stripped from get() and rejected
by update().
"""

import os
from typing import Any

import structlog

from embry0.storage.database import DatabasePool

logger = structlog.get_logger(__name__)

_HARDCODED_FALLBACKS = {
    "model_heavy": "claude-opus-5",
    "model_medium": "claude-sonnet-5",
    "model_light": "claude-haiku-4-5",
    "default_model": "",
}

# RAV-1297: disabled_models (JSONB list of catalog model ids) rides the same
# single row — no env-derived default; absent row means "nothing disabled".
# fallback_models (the provider fallback order) likewise: absent means off.
_UPDATABLE_FIELDS = frozenset(_HARDCODED_FALLBACKS.keys()) | {"disabled_models", "fallback_models"}

# Legacy columns still present in the provider_config table but no longer
# part of the config surface (RAV-1239).
_RETIRED_COLUMNS = ("provider_mode", "ollama_base_url")


def _env_derived_defaults() -> dict[str, Any]:
    """Read defaults from env at call time, falling back to hardcoded values."""
    return {
        "model_heavy": os.environ.get("MODEL_HEAVY", _HARDCODED_FALLBACKS["model_heavy"]),
        "model_medium": os.environ.get("MODEL_MEDIUM", _HARDCODED_FALLBACKS["model_medium"]),
        "model_light": os.environ.get("MODEL_LIGHT", _HARDCODED_FALLBACKS["model_light"]),
        "default_model": os.environ.get("DEFAULT_MODEL", _HARDCODED_FALLBACKS["default_model"]),
    }


class ProviderConfigRepository:
    """CRUD for provider configuration."""

    def __init__(self, db: DatabasePool) -> None:
        self._db = db

    async def get(self) -> dict[str, Any]:
        """Get current provider config. Falls back to env-derived defaults if no row exists.

        Adds derived booleans api_key_set and oauth_token_set from environment
        variables. Strips id and updated_at from the returned dict.
        """
        row = await self._db.fetchrow("SELECT * FROM provider_config WHERE id = 'default'")
        if row:
            data = dict(row)
            data.pop("id", None)
            data.pop("updated_at", None)
            # Remove DB-stored booleans; they will be derived below
            data.pop("api_key_set", None)
            data.pop("oauth_token_set", None)
            for retired in _RETIRED_COLUMNS:
                data.pop(retired, None)
        else:
            data = _env_derived_defaults()

        # Normalize regardless of source: absent column / no row = all enabled.
        raw_disabled = data.get("disabled_models")
        data["disabled_models"] = list(raw_disabled) if isinstance(raw_disabled, list) else []
        raw_fallback = data.get("fallback_models")
        data["fallback_models"] = list(raw_fallback) if isinstance(raw_fallback, list) else []

        data["api_key_set"] = bool(os.environ.get("ANTHROPIC_API_KEY"))
        # Historically CLAUDE_MAX_OAUTH_TOKEN; renamed to CLAUDE_CODE_OAUTH_TOKEN
        # in 2026-04-28 storage hardening (Plan D) to match auth_provider.py and CLAUDE.md.
        data["oauth_token_set"] = bool(os.environ.get("CLAUDE_CODE_OAUTH_TOKEN"))
        return data

    async def update(self, **fields: Any) -> dict[str, Any]:
        """Update provider config fields. Creates default row if needed.

        Only fields in _UPDATABLE_FIELDS are accepted; api_key_set and
        oauth_token_set are ignored if passed. Returns the updated config.
        """
        valid = {k: v for k, v in fields.items() if k in _UPDATABLE_FIELDS}
        if not valid:
            return await self.get()

        await self._db.execute(
            "INSERT INTO provider_config (id) VALUES ('default') ON CONFLICT (id) DO NOTHING",
        )
        sets: list[str] = []
        args: list[Any] = []
        idx = 1
        for key, value in valid.items():
            sets.append(f"{key} = ${idx}")
            args.append(value)
            idx += 1
        sets.append("updated_at = NOW()")
        await self._db.execute(
            f"UPDATE provider_config SET {', '.join(sets)} WHERE id = 'default'",
            *args,
        )
        logger.info("provider_config_updated", fields=list(valid.keys()))
        return await self.get()

    async def test_connection(self) -> dict[str, str]:
        """Report whether an Anthropic credential is configured.

        RAV-1239: with provider_mode gone, agents run under whatever
        auth_mode resolves per repo/job/agent, so the meaningful check is
        "can any Anthropic-backed agent authenticate at all". Returns
        {"status": "ok"|"error", "message": str}.
        """
        config = await self.get()

        if config.get("oauth_token_set") and config.get("api_key_set"):
            return {"status": "ok", "message": "Claude Code OAuth token and Anthropic API key are configured."}
        if config.get("oauth_token_set"):
            return {"status": "ok", "message": "Claude Code OAuth token is configured."}
        if config.get("api_key_set"):
            return {"status": "ok", "message": "Anthropic API key is configured."}
        return {
            "status": "error",
            "message": "Neither CLAUDE_CODE_OAUTH_TOKEN nor ANTHROPIC_API_KEY is set.",
        }
