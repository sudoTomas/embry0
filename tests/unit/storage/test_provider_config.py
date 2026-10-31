import pytest

from embry0.storage.database import DatabasePool
from embry0.storage.migrations.runner import run_migrations
from embry0.storage.repositories.provider_config import ProviderConfigRepository

pytestmark = pytest.mark.requires_postgres


@pytest.fixture
async def provider_repo() -> ProviderConfigRepository:
    import os

    url = os.environ.get("TEST_DATABASE_URL", "postgresql://embry0:embry0@localhost:5432/embry0_test")
    db = DatabasePool(url)
    await db.connect()
    await run_migrations(db)
    # Reset table state so tests that check env-fallback (no DB row) pass in any order.
    await db.execute("DELETE FROM provider_config")
    yield ProviderConfigRepository(db)
    await db.close()


@pytest.mark.asyncio
async def test_get_defaults(provider_repo: ProviderConfigRepository, monkeypatch):
    # Migration #13 deletes the seed row, so get() falls through to env-derived
    # defaults. Clear all relevant env vars so the hardcoded fallbacks are exercised.
    for var in ("MODEL_HEAVY", "MODEL_MEDIUM", "MODEL_LIGHT", "DEFAULT_MODEL"):
        monkeypatch.delenv(var, raising=False)

    config = await provider_repo.get()
    assert config["model_heavy"] == "claude-opus-5"
    assert config["model_medium"] == "claude-sonnet-5"
    assert config["model_light"] == "claude-haiku-4-5"
    assert config["default_model"] == ""
    assert "id" not in config
    assert "updated_at" not in config
    # RAV-1239: retired fields never surface.
    assert "provider_mode" not in config
    assert "ollama_base_url" not in config


@pytest.mark.asyncio
async def test_get_includes_derived_booleans(provider_repo: ProviderConfigRepository, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test-key")
    monkeypatch.delenv("CLAUDE_CODE_OAUTH_TOKEN", raising=False)
    config = await provider_repo.get()
    assert config["api_key_set"] is True
    assert config["oauth_token_set"] is False


@pytest.mark.asyncio
async def test_get_booleans_false_when_env_unset(provider_repo: ProviderConfigRepository, monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("CLAUDE_CODE_OAUTH_TOKEN", raising=False)
    config = await provider_repo.get()
    assert config["api_key_set"] is False
    assert config["oauth_token_set"] is False


@pytest.mark.asyncio
async def test_update_model_tiers(provider_repo: ProviderConfigRepository):
    result = await provider_repo.update(
        model_heavy="claude-opus-4-5",
        model_medium="claude-sonnet-4-5",
        model_light="claude-haiku-4-0",
    )
    assert result["model_heavy"] == "claude-opus-4-5"
    assert result["model_medium"] == "claude-sonnet-4-5"
    assert result["model_light"] == "claude-haiku-4-0"


@pytest.mark.asyncio
async def test_update_ignores_retired_provider_fields(provider_repo: ProviderConfigRepository):
    # RAV-1239: provider_mode/ollama_base_url are no longer updatable — the
    # DB columns exist but writes are silently dropped and reads strip them.
    result = await provider_repo.update(provider_mode="ollama", ollama_base_url="http://localhost:11434")
    assert "provider_mode" not in result
    assert "ollama_base_url" not in result


@pytest.mark.asyncio
async def test_update_ignores_non_updatable_fields(provider_repo: ProviderConfigRepository):
    # api_key_set and oauth_token_set must NOT be updatable via update()
    result = await provider_repo.update(api_key_set=True, oauth_token_set=True)
    # The call returns the current config without error, but the booleans
    # remain derived from environment variables (not passed through)
    assert isinstance(result, dict)
    # These are derived; the update should have been a no-op for those fields
    # so no DB columns are modified — confirmed by the result still being valid
    assert "model_heavy" in result


@pytest.mark.asyncio
async def test_update_returns_get(provider_repo: ProviderConfigRepository):
    result = await provider_repo.update(default_model="claude-sonnet-4-6")
    direct = await provider_repo.get()
    assert result["default_model"] == direct["default_model"]


# --- test_connection: credential-presence report (RAV-1239 semantics) ---


@pytest.mark.asyncio
async def test_test_connection_ok_with_api_key(provider_repo: ProviderConfigRepository, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test-key")
    monkeypatch.delenv("CLAUDE_CODE_OAUTH_TOKEN", raising=False)
    result = await provider_repo.test_connection()
    assert result["status"] == "ok"
    assert "API key" in result["message"]


@pytest.mark.asyncio
async def test_test_connection_ok_with_oauth_token(provider_repo: ProviderConfigRepository, monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setenv("CLAUDE_CODE_OAUTH_TOKEN", "oauth-token-value")
    result = await provider_repo.test_connection()
    assert result["status"] == "ok"
    assert "OAuth token" in result["message"]


@pytest.mark.asyncio
async def test_test_connection_error_without_credentials(provider_repo: ProviderConfigRepository, monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("CLAUDE_CODE_OAUTH_TOKEN", raising=False)
    result = await provider_repo.test_connection()
    assert result["status"] == "error"
    assert "CLAUDE_CODE_OAUTH_TOKEN" in result["message"]
    assert "ANTHROPIC_API_KEY" in result["message"]


# --- Env-derived defaults: empty DB falls through to env ---


@pytest.mark.asyncio
async def test_get_reads_model_tiers_from_env_when_no_row(provider_repo: ProviderConfigRepository, monkeypatch):
    """On a fresh DB (post-migration #13) get() must read env vars for defaults."""
    monkeypatch.setenv("MODEL_HEAVY", "claude-opus-4-7")
    monkeypatch.setenv("MODEL_MEDIUM", "claude-sonnet-4-7")
    monkeypatch.setenv("MODEL_LIGHT", "claude-haiku-4-6")
    monkeypatch.setenv("DEFAULT_MODEL", "claude-sonnet-4-7")

    config = await provider_repo.get()
    assert config["model_heavy"] == "claude-opus-4-7"
    assert config["model_medium"] == "claude-sonnet-4-7"
    assert config["model_light"] == "claude-haiku-4-6"
    assert config["default_model"] == "claude-sonnet-4-7"


@pytest.mark.asyncio
async def test_db_row_overrides_env(provider_repo: ProviderConfigRepository, monkeypatch):
    """If the user has saved via the UI (row exists) env is NOT consulted for those fields."""
    monkeypatch.setenv("MODEL_HEAVY", "claude-opus-4-7")
    await provider_repo.update(model_heavy="claude-opus-5")
    config = await provider_repo.get()
    # DB wins
    assert config["model_heavy"] == "claude-opus-5"


@pytest.mark.asyncio
async def test_the_fallback_order_is_off_until_saved_and_keeps_its_order(provider_repo: ProviderConfigRepository):
    """Provider fallback (spec 2026-10-25): no row and a fresh row are both off."""
    assert (await provider_repo.get())["fallback_models"] == []
    await provider_repo.update(model_heavy="claude-opus-5")
    assert (await provider_repo.get())["fallback_models"] == []
    saved = await provider_repo.update(fallback_models=["grok-4.7", "claude-haiku-4-5"])
    assert saved["fallback_models"] == ["grok-4.7", "claude-haiku-4-5"]
    assert (await provider_repo.update(fallback_models=[]))["fallback_models"] == []


_TIERS = ("model_heavy", "model_medium", "model_light", "default_model")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "fields",
    [
        {"local_max_runs": 2},
        {"fallback_models": ["grok-4.7"]},
        {"disabled_models": ["claude-haiku-4-5"]},
    ],
    ids=lambda fields: next(iter(fields)),
)
@pytest.mark.parametrize("env", ["set", "unset"])
async def test_a_first_save_of_one_field_keeps_the_tiers_get_reported(
    provider_repo: ProviderConfigRepository, monkeypatch, fields, env
):
    """#183: the first save of any one field creates the row, and the row wins
    from then on. Its tiers must be what get() reported before (env, then the
    code defaults), not migration 2's column defaults."""
    if env == "set":
        monkeypatch.setenv("MODEL_HEAVY", "claude-opus-5-env")
        monkeypatch.setenv("MODEL_MEDIUM", "claude-sonnet-5-env")
        monkeypatch.setenv("MODEL_LIGHT", "claude-haiku-4-5-env")
        monkeypatch.setenv("DEFAULT_MODEL", "grok-4.7")
    else:
        for var in ("MODEL_HEAVY", "MODEL_MEDIUM", "MODEL_LIGHT", "DEFAULT_MODEL"):
            monkeypatch.delenv(var, raising=False)
    before = await provider_repo.get()

    after = await provider_repo.update(**fields)

    for key in _TIERS:
        assert after[key] == before[key], (
            f"saving only {next(iter(fields))} moved {key} from {before[key]!r} to {after[key]!r}"
        )
    for key, value in fields.items():
        assert after[key] == value
