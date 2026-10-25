"""PUT /config/provider and local models: the run cap, the watcher's local-first model, and the fallback order."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from httpx import ASGITransport, AsyncClient

from embry0.api.app import create_app
from embry0.config import Embry0Config


@pytest.fixture
def app(monkeypatch):
    monkeypatch.setenv("OPENAI_COMPATIBLE_PROVIDERS", "custom")
    app = create_app(Embry0Config(_env_file=None, auth_dev_mode=True, webhook_dev_mode=True))
    provider_repo = MagicMock()
    provider_repo.get = AsyncMock(return_value={"disabled_models": [], "fallback_models": []})
    provider_repo.update = AsyncMock(return_value={})
    app.state.provider_repo = provider_repo
    return app


async def _put(app: Any, body: dict[str, Any]):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        return await client.put("/api/v1/config/provider", json=body, headers={"X-Requested-With": "XMLHttpRequest"})


async def test_the_cap_and_the_watchers_local_model_are_saved(app) -> None:
    resp = await _put(app, {"local_max_runs": 2, "watcher_local_model": " custom/qwen "})
    assert resp.status_code == 200, resp.text
    app.state.provider_repo.update.assert_awaited_once_with(local_max_runs=2, watcher_local_model="custom/qwen")


async def test_an_empty_watcher_model_turns_local_first_off(app) -> None:
    resp = await _put(app, {"watcher_local_model": ""})
    assert resp.status_code == 200, resp.text
    app.state.provider_repo.update.assert_awaited_once_with(watcher_local_model="")


@pytest.mark.parametrize("cap", [0, 65, -1])
async def test_a_cap_outside_one_to_sixty_four_is_refused(app, cap) -> None:
    assert (await _put(app, {"local_max_runs": cap})).status_code == 422
    app.state.provider_repo.update.assert_not_awaited()


@pytest.mark.parametrize("model", ["claude-sonnet-5", "grok-4.7", "custom/", "qwen"])
async def test_a_watcher_model_that_is_not_the_local_servers_is_refused(app, model) -> None:
    resp = await _put(app, {"watcher_local_model": model})
    assert resp.status_code == 422, resp.text
    assert "custom/<model>" in resp.json()["detail"]
    app.state.provider_repo.update.assert_not_awaited()


async def test_the_local_model_can_end_the_fallback_order(app) -> None:
    resp = await _put(app, {"fallback_models": ["grok-4.7", "custom/qwen"]})
    assert resp.status_code == 200, resp.text
    app.state.provider_repo.update.assert_awaited_once_with(fallback_models=["grok-4.7", "custom/qwen"])


async def test_with_the_custom_provider_off_a_local_model_is_refused(app, monkeypatch) -> None:
    monkeypatch.setenv("OPENAI_COMPATIBLE_PROVIDERS", "")
    assert (await _put(app, {"watcher_local_model": "custom/qwen"})).status_code == 422
    assert (await _put(app, {"fallback_models": ["custom/qwen"]})).status_code == 422
