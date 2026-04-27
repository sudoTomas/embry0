"""Provider config repository — AI provider and model tier settings.

Defaults are derived from environment variables on every read, so changes
to ``.env`` flow through to the Settings UI on a fresh DB. Once the user
saves changes via the UI a row exists in ``provider_config`` and that row
takes precedence forever.
"""

import os
from typing import Any

import structlog

from athanor.storage.database import DatabasePool

logger = structlog.get_logger(__name__)

_HARDCODED_FALLBACKS = {
    "provider_mode": "anthropic_api",
    "model_heavy": "claude-opus-4-7",
    "model_medium": "claude-sonnet-4-6",
    "model_light": "claude-haiku-4-5",
    "default_model": "",
    "ollama_base_url": "",
}

_UPDATABLE_FIELDS = frozenset(_HARDCODED_FALLBACKS.keys())


def _env_derived_defaults() -> dict[str, Any]:
    """Read defaults from env at call time, falling back to hardcoded values."""
    return {
        "provider_mode": os.environ.get("PROVIDER_MODE", _HARDCODED_FALLBACKS["provider_mode"]),
        "model_heavy": os.environ.get("MODEL_HEAVY", _HARDCODED_FALLBACKS["model_heavy"]),
        "model_medium": os.environ.get("MODEL_MEDIUM", _HARDCODED_FALLBACKS["model_medium"]),
        "model_light": os.environ.get("MODEL_LIGHT", _HARDCODED_FALLBACKS["model_light"]),
        "default_model": os.environ.get("DEFAULT_MODEL", _HARDCODED_FALLBACKS["default_model"]),
        "ollama_base_url": os.environ.get("OLLAMA_BASE_URL", _HARDCODED_FALLBACKS["ollama_base_url"]),
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
        else:
            data = _env_derived_defaults()

        data["api_key_set"] = bool(os.environ.get("ANTHROPIC_API_KEY"))
        data["oauth_token_set"] = bool(os.environ.get("CLAUDE_MAX_OAUTH_TOKEN"))
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
        """Test connectivity for the configured provider.

        For anthropic_api/claude_max: verifies credentials are set.
        For ollama: makes an HTTP request to verify server is reachable.
        Returns {"status": "ok"|"error", "message": str}.
        """
        config = await self.get()
        mode = config.get("provider_mode", "")

        if mode == "anthropic_api":
            if not config.get("api_key_set"):
                return {"status": "error", "message": "ANTHROPIC_API_KEY environment variable is not set."}
            return {"status": "ok", "message": "Anthropic API key is configured."}

        if mode == "claude_max":
            if not config.get("oauth_token_set"):
                return {"status": "error", "message": "CLAUDE_MAX_OAUTH_TOKEN environment variable is not set."}
            return {"status": "ok", "message": "Claude Max OAuth token is configured."}

        if mode == "ollama":
            base_url = config.get("ollama_base_url", "")
            if not base_url:
                return {"status": "error", "message": "ollama_base_url is not configured."}
            try:
                import aiohttp

                async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=5)) as session:
                    async with session.get(f"{base_url.rstrip('/')}/api/tags") as resp:
                        if resp.status == 200:
                            return {"status": "ok", "message": f"Ollama server reachable at {base_url}"}
                        return {"status": "error", "message": f"Ollama returned HTTP {resp.status}"}
            except Exception as exc:
                return {"status": "error", "message": f"Cannot reach Ollama at {base_url}: {exc}"}

        return {"status": "error", "message": f"Unknown provider mode: {mode!r}"}
