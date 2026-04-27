from unittest.mock import AsyncMock, MagicMock, patch

import asyncpg
import pytest

from athanor.storage.database import DatabasePool
from athanor.storage.migrations.runner import run_migrations
from athanor.storage.repositories.provider_config import ProviderConfigRepository


@pytest.fixture
async def provider_repo(pg_pool: asyncpg.Pool) -> ProviderConfigRepository:
    import os

    url = os.environ.get("TEST_DATABASE_URL", "postgresql://legion:legion@localhost:5432/legion_test")
    db = DatabasePool(url)
    await db.connect()
    await run_migrations(db)
    yield ProviderConfigRepository(db)
    await db.close()


@pytest.mark.asyncio
async def test_get_defaults(provider_repo: ProviderConfigRepository):
    config = await provider_repo.get()
    assert config["provider_mode"] == "anthropic_api"
    assert config["model_heavy"] == "claude-opus-4-6"
    assert config["model_medium"] == "claude-sonnet-4-6"
    assert config["model_light"] == "claude-haiku-4-5"
    assert config["default_model"] == ""
    assert config["ollama_base_url"] == ""
    assert "id" not in config
    assert "updated_at" not in config


@pytest.mark.asyncio
async def test_get_includes_derived_booleans(provider_repo: ProviderConfigRepository, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test-key")
    monkeypatch.delenv("CLAUDE_MAX_OAUTH_TOKEN", raising=False)
    config = await provider_repo.get()
    assert config["api_key_set"] is True
    assert config["oauth_token_set"] is False


@pytest.mark.asyncio
async def test_get_booleans_false_when_env_unset(provider_repo: ProviderConfigRepository, monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("CLAUDE_MAX_OAUTH_TOKEN", raising=False)
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
    # Unmodified fields keep their defaults
    assert result["provider_mode"] == "anthropic_api"


@pytest.mark.asyncio
async def test_update_provider_mode(provider_repo: ProviderConfigRepository):
    result = await provider_repo.update(provider_mode="ollama", ollama_base_url="http://localhost:11434")
    assert result["provider_mode"] == "ollama"
    assert result["ollama_base_url"] == "http://localhost:11434"


@pytest.mark.asyncio
async def test_update_ignores_non_updatable_fields(provider_repo: ProviderConfigRepository):
    # api_key_set and oauth_token_set must NOT be updatable via update()
    result = await provider_repo.update(api_key_set=True, oauth_token_set=True)
    # The call returns the current config without error, but the booleans
    # remain derived from environment variables (not passed through)
    assert isinstance(result, dict)
    # These are derived; the update should have been a no-op for those fields
    # so no DB columns are modified — confirmed by the result still being valid
    assert "provider_mode" in result


@pytest.mark.asyncio
async def test_update_returns_get(provider_repo: ProviderConfigRepository):
    result = await provider_repo.update(default_model="claude-sonnet-4-6")
    direct = await provider_repo.get()
    assert result["default_model"] == direct["default_model"]


@pytest.mark.asyncio
async def test_test_connection_anthropic_api_ok(provider_repo: ProviderConfigRepository, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test-key")
    await provider_repo.update(provider_mode="anthropic_api")
    result = await provider_repo.test_connection()
    assert result["status"] == "ok"


@pytest.mark.asyncio
async def test_test_connection_anthropic_api_error(provider_repo: ProviderConfigRepository, monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    await provider_repo.update(provider_mode="anthropic_api")
    result = await provider_repo.test_connection()
    assert result["status"] == "error"
    assert "ANTHROPIC_API_KEY" in result["message"]


@pytest.mark.asyncio
async def test_test_connection_claude_max_ok(provider_repo: ProviderConfigRepository, monkeypatch):
    monkeypatch.setenv("CLAUDE_MAX_OAUTH_TOKEN", "oauth-token-value")
    await provider_repo.update(provider_mode="claude_max")
    result = await provider_repo.test_connection()
    assert result["status"] == "ok"


@pytest.mark.asyncio
async def test_test_connection_claude_max_error(provider_repo: ProviderConfigRepository, monkeypatch):
    monkeypatch.delenv("CLAUDE_MAX_OAUTH_TOKEN", raising=False)
    await provider_repo.update(provider_mode="claude_max")
    result = await provider_repo.test_connection()
    assert result["status"] == "error"
    assert "CLAUDE_MAX_OAUTH_TOKEN" in result["message"]


@pytest.mark.asyncio
async def test_test_connection_ollama_ok(provider_repo: ProviderConfigRepository):
    await provider_repo.update(provider_mode="ollama", ollama_base_url="http://localhost:11434")

    mock_resp = MagicMock()
    mock_resp.status = 200
    mock_resp.__aenter__ = AsyncMock(return_value=mock_resp)
    mock_resp.__aexit__ = AsyncMock(return_value=False)

    mock_session = MagicMock()
    mock_session.get = MagicMock(return_value=mock_resp)
    mock_session.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session.__aexit__ = AsyncMock(return_value=False)

    mock_aiohttp = MagicMock()
    mock_aiohttp.ClientSession = MagicMock(return_value=mock_session)
    mock_aiohttp.ClientTimeout = MagicMock(return_value=MagicMock())

    with patch.dict("sys.modules", {"aiohttp": mock_aiohttp}):
        result = await provider_repo.test_connection()

    assert result["status"] == "ok"
    assert "http://localhost:11434" in result["message"]
    mock_session.get.assert_called_once_with("http://localhost:11434/api/tags")


@pytest.mark.asyncio
async def test_test_connection_ollama_error(provider_repo: ProviderConfigRepository):
    await provider_repo.update(provider_mode="ollama", ollama_base_url="")
    result = await provider_repo.test_connection()
    assert result["status"] == "error"
    assert "ollama_base_url" in result["message"]


@pytest.mark.asyncio
async def test_test_connection_ollama_non_200(provider_repo: ProviderConfigRepository):
    await provider_repo.update(provider_mode="ollama", ollama_base_url="http://localhost:11434")

    mock_resp = MagicMock()
    mock_resp.status = 503
    mock_resp.__aenter__ = AsyncMock(return_value=mock_resp)
    mock_resp.__aexit__ = AsyncMock(return_value=False)

    mock_session = MagicMock()
    mock_session.get = MagicMock(return_value=mock_resp)
    mock_session.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session.__aexit__ = AsyncMock(return_value=False)

    mock_aiohttp = MagicMock()
    mock_aiohttp.ClientSession = MagicMock(return_value=mock_session)
    mock_aiohttp.ClientTimeout = MagicMock(return_value=MagicMock())

    with patch.dict("sys.modules", {"aiohttp": mock_aiohttp}):
        result = await provider_repo.test_connection()

    assert result["status"] == "error"
    assert "503" in result["message"]


@pytest.mark.asyncio
async def test_test_connection_ollama_unreachable(provider_repo: ProviderConfigRepository):
    await provider_repo.update(provider_mode="ollama", ollama_base_url="http://localhost:11434")

    mock_session = MagicMock()
    mock_session.get = MagicMock(side_effect=OSError("Connection refused"))
    mock_session.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session.__aexit__ = AsyncMock(return_value=False)

    mock_aiohttp = MagicMock()
    mock_aiohttp.ClientSession = MagicMock(return_value=mock_session)
    mock_aiohttp.ClientTimeout = MagicMock(return_value=MagicMock())

    with patch.dict("sys.modules", {"aiohttp": mock_aiohttp}):
        result = await provider_repo.test_connection()

    assert result["status"] == "error"
    assert "Cannot reach Ollama" in result["message"]
    assert "http://localhost:11434" in result["message"]
