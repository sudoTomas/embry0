from unittest.mock import AsyncMock, MagicMock

import pytest
from httpx import ASGITransport, AsyncClient

from embry0.api.app import create_app
from embry0.config import Embry0Config

_GET_RESPONSE = {
    "trigger_labels": ["embry0"],
    "webhook_secret_set": False,
    "slack_webhook_url_set": False,
    "slack_webhook_url_masked": "",
    "telegram_bot_token_set": False,
    "telegram_bot_token_masked": "",
    "telegram_chat_id": "",
}

_UPDATE_RESPONSE = {
    "trigger_labels": ["embry0", "AutoFix"],
    "webhook_secret_set": False,
    "slack_webhook_url_set": False,
    "slack_webhook_url_masked": "",
    "telegram_bot_token_set": False,
    "telegram_bot_token_masked": "",
    "telegram_chat_id": "",
}


@pytest.fixture
def app():
    config = Embry0Config(_env_file=None, auth_dev_mode=True, webhook_dev_mode=True)
    app = create_app(config)
    repo = MagicMock()
    repo.get = AsyncMock(return_value=_GET_RESPONSE)
    repo.update = AsyncMock(return_value=_UPDATE_RESPONSE)
    app.state.integration_repo = repo
    return app


@pytest.mark.asyncio
async def test_get_integrations(app):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/api/v1/config/integrations")
    assert resp.status_code == 200
    assert resp.json()["trigger_labels"] == ["embry0"]


@pytest.mark.asyncio
async def test_update_integrations(app):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.put(
            "/api/v1/config/integrations",
            json={"trigger_labels": ["embry0", "AutoFix"]},
            headers={"X-Requested-With": "XMLHttpRequest"},
        )
    assert resp.status_code == 200
    assert resp.json()["trigger_labels"] == ["embry0", "AutoFix"]
