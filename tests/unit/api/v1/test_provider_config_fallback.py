"""PUT /config/provider and the provider fallback order (spec 2026-10-25)."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from httpx import ASGITransport, AsyncClient

from embry0.api.app import create_app
from embry0.config import Embry0Config


@pytest.fixture
def app(monkeypatch):
    monkeypatch.setenv("OPENAI_COMPATIBLE_PROVIDERS", "openai,openrouter")
    app = create_app(Embry0Config(_env_file=None, auth_dev_mode=True, webhook_dev_mode=True))
    provider_repo = MagicMock()
    provider_repo.get = AsyncMock(return_value={"disabled_models": ["grok-4.5"], "fallback_models": ["grok-4.7"]})
    provider_repo.update = AsyncMock(return_value={})
    app.state.provider_repo = provider_repo
    return app


async def _put(app: Any, body: dict[str, Any]):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        return await client.put("/api/v1/config/provider", json=body, headers={"X-Requested-With": "XMLHttpRequest"})


async def test_an_order_of_runnable_models_is_saved(app) -> None:
    resp = await _put(app, {"fallback_models": ["grok-4.7", "openrouter/openai/gpt-6.1-sol"]})
    assert resp.status_code == 200, resp.text
    app.state.provider_repo.update.assert_awaited_once_with(
        fallback_models=["grok-4.7", "openrouter/openai/gpt-6.1-sol"]
    )


async def test_an_empty_order_turns_it_off(app) -> None:
    resp = await _put(app, {"fallback_models": []})
    assert resp.status_code == 200, resp.text
    app.state.provider_repo.update.assert_awaited_once_with(fallback_models=[])


@pytest.mark.parametrize(
    ("order", "words"),
    [
        (["not-a-model"], "unknown"),
        (["grok-4.7", "grok-4.7"], "more than once"),
        (["gpt-6.1-sol"], "chat-only"),
        (["grok-4.5"], "cannot be disabled"),
    ],
)
async def test_an_order_an_agent_could_not_run_is_refused(app, order, words) -> None:
    resp = await _put(app, {"fallback_models": order})
    assert resp.status_code == 422, resp.text
    assert words in resp.json()["detail"]
    app.state.provider_repo.update.assert_not_awaited()


async def test_a_model_the_order_names_cannot_be_disabled(app) -> None:
    resp = await _put(app, {"disabled_models": ["grok-4.7"]})
    assert resp.status_code == 422, resp.text
    assert "fallback" in resp.json()["detail"]
    ok = await _put(app, {"disabled_models": ["grok-4.5", "claude-haiku-4-5"]})
    assert ok.status_code == 200, ok.text
