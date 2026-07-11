from unittest.mock import AsyncMock, MagicMock

import pytest
from httpx import ASGITransport, AsyncClient

from embry0.api.app import create_app
from embry0.config import Embry0Config

_GET_RESPONSE = {
    "provider_mode": "anthropic_api",
    "model_heavy": "claude-opus-4-6",
    "model_medium": "claude-sonnet-4-6",
    "model_light": "claude-haiku-4-5",
    "default_model": "",
    "ollama_base_url": "",
    "api_key_set": False,
    "oauth_token_set": False,
}

_UPDATE_RESPONSE = {
    **_GET_RESPONSE,
    "model_heavy": "claude-opus-4-7",
}


@pytest.fixture
def app():
    config = Embry0Config(_env_file=None, auth_dev_mode=True, webhook_dev_mode=True)
    app = create_app(config)
    repo = MagicMock()
    repo.get = AsyncMock(return_value=_GET_RESPONSE)
    repo.update = AsyncMock(return_value=_UPDATE_RESPONSE)
    repo.test_connection = AsyncMock(return_value={"status": "ok", "message": "Anthropic API key is set."})
    app.state.provider_repo = repo
    return app


@pytest.mark.asyncio
async def test_get_provider(app):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/api/v1/config/provider")
    assert resp.status_code == 200
    assert resp.json()["model_heavy"] == "claude-opus-4-6"


@pytest.mark.asyncio
async def test_update_provider(app):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.put(
            "/api/v1/config/provider",
            json={"model_heavy": "claude-opus-4-7"},
            headers={"X-Requested-With": "XMLHttpRequest"},
        )
    assert resp.status_code == 200
    assert resp.json()["model_heavy"] == "claude-opus-4-7"


@pytest.mark.asyncio
async def test_provider_test_connection(app):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post(
            "/api/v1/config/provider/test",
            headers={"X-Requested-With": "XMLHttpRequest"},
        )
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"
