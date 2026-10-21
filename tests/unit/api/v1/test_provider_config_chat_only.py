"""PUT /config/provider refuses a chat-only model where agents with tools would run on it."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from httpx import ASGITransport, AsyncClient

from embry0.api.app import create_app
from embry0.config import Embry0Config


@pytest.fixture
def app(monkeypatch):
    monkeypatch.setenv("OPENAI_COMPATIBLE_PROVIDERS", "openai")
    app = create_app(Embry0Config(_env_file=None, auth_dev_mode=True, webhook_dev_mode=True))
    provider_repo = MagicMock()
    provider_repo.get = AsyncMock(return_value={"disabled_models": []})
    provider_repo.update = AsyncMock(return_value={})
    app.state.provider_repo = provider_repo
    return app


async def _put(app: Any, body: dict[str, Any]):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        return await client.put("/api/v1/config/provider", json=body, headers={"X-Requested-With": "XMLHttpRequest"})


@pytest.mark.parametrize("key", ["default_model", "model_heavy", "model_medium", "model_light"])
async def test_a_chat_only_model_is_refused_as_the_default_or_a_tier(app, key) -> None:
    resp = await _put(app, {key: "gpt-6.1-sol"})
    assert resp.status_code == 422, resp.text
    assert "chat-only" in resp.json()["detail"] and "gpt-6.1-sol" in resp.json()["detail"]
    app.state.provider_repo.update.assert_not_awaited()


async def test_a_model_with_tool_calling_is_accepted_as_the_default(app) -> None:
    resp = await _put(app, {"default_model": "gpt-6-luna"})
    assert resp.status_code == 200, resp.text
    app.state.provider_repo.update.assert_awaited()
