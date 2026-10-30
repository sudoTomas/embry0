"""PUT /config/provider and local models: the run cap, the fallback order, and the watcher's model that moved (#164)."""

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


async def test_the_cap_is_saved(app) -> None:
    resp = await _put(app, {"local_max_runs": 2})
    assert resp.status_code == 200, resp.text
    app.state.provider_repo.update.assert_awaited_once_with(local_max_runs=2)


@pytest.mark.parametrize("model", ["custom/qwen", "", "claude-sonnet-5"])
async def test_the_watchers_model_is_refused_with_where_it_went(app, model) -> None:
    """#164: the field moved to the settings document's background_models section. A client still sending
    it learns where it went, and nothing is stored, the cap sent with it included."""
    resp = await _put(app, {"local_max_runs": 2, "watcher_local_model": model})
    assert resp.status_code == 422, f"watcher_local_model is refused, not dropped: {resp.text}"
    detail = resp.json()["detail"]
    assert "background_models" in detail and "/config/settings/background_models" in detail, detail
    app.state.provider_repo.update.assert_not_awaited()


@pytest.mark.parametrize("cap", [0, 65, -1])
async def test_a_cap_outside_one_to_sixty_four_is_refused(app, cap) -> None:
    assert (await _put(app, {"local_max_runs": cap})).status_code == 422
    app.state.provider_repo.update.assert_not_awaited()


async def test_the_local_model_can_end_the_fallback_order(app) -> None:
    resp = await _put(app, {"fallback_models": ["grok-4.7", "custom/qwen"]})
    assert resp.status_code == 200, resp.text
    app.state.provider_repo.update.assert_awaited_once_with(fallback_models=["grok-4.7", "custom/qwen"])


async def test_with_the_custom_provider_off_a_local_model_is_refused(app, monkeypatch) -> None:
    monkeypatch.setenv("OPENAI_COMPATIBLE_PROVIDERS", "")
    assert (await _put(app, {"fallback_models": ["custom/qwen"]})).status_code == 422
