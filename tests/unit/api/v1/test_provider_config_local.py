"""PUT /config/provider and local models: the fallback order, and the run cap (#174) and the watcher's model (#164)
that moved to the settings document."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from httpx import ASGITransport, AsyncClient

from embry0.api.app import create_app
from embry0.config import Embry0Config
from tests.fixtures.provider_catalogue import LOCAL_SERVER, enable_providers


@pytest.fixture
def app(monkeypatch):
    enable_providers(monkeypatch, "custom", **LOCAL_SERVER)
    app = create_app(Embry0Config(_env_file=None, auth_dev_mode=True, webhook_dev_mode=True))
    provider_repo = MagicMock()
    provider_repo.get = AsyncMock(return_value={"disabled_models": [], "fallback_models": []})
    provider_repo.update = AsyncMock(return_value={})
    app.state.provider_repo = provider_repo
    return app


async def _put(app: Any, body: dict[str, Any]):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        return await client.put("/api/v1/config/provider", json=body, headers={"X-Requested-With": "XMLHttpRequest"})


@pytest.mark.parametrize("cap", [2, 1, 0, 65, None, "three"])
async def test_the_run_cap_is_refused_with_where_it_went(app, cap) -> None:
    """#174: how many calls each server takes at once is the settings document's local_models.max_runs. A client
    still sending local_max_runs learns where it went, and nothing is stored, the fields sent with it included."""
    resp = await _put(app, {"local_max_runs": cap, "fallback_models": ["custom/qwen"]})
    assert resp.status_code == 422, f"local_max_runs is refused, not dropped: {resp.text}"
    detail = resp.json()["detail"]
    assert isinstance(detail, str) and "local_models" in detail and "/config/settings/local_models" in detail, detail
    app.state.provider_repo.update.assert_not_awaited()


@pytest.mark.parametrize("model", ["custom/qwen", "", "claude-sonnet-5", None, 7])
async def test_the_watchers_model_is_refused_with_where_it_went(app, model) -> None:
    """#164: the field moved to the settings document's background_models section. A client still sending
    it learns where it went, and nothing is stored, the cap sent with it included."""
    resp = await _put(app, {"local_max_runs": 2, "watcher_local_model": model})
    assert resp.status_code == 422, f"watcher_local_model is refused, not dropped: {resp.text}"
    detail = resp.json()["detail"]
    assert "background_models" in detail and "/config/settings/background_models" in detail, detail
    app.state.provider_repo.update.assert_not_awaited()


async def test_the_local_model_can_end_the_fallback_order(app) -> None:
    resp = await _put(app, {"fallback_models": ["grok-4.7", "custom/qwen"]})
    assert resp.status_code == 200, resp.text
    app.state.provider_repo.update.assert_awaited_once_with(fallback_models=["grok-4.7", "custom/qwen"])


async def test_with_the_custom_provider_off_a_local_model_is_refused(app, monkeypatch) -> None:
    enable_providers(monkeypatch)
    assert (await _put(app, {"fallback_models": ["custom/qwen"]})).status_code == 422


@pytest.mark.parametrize("field", ["local_max_runs", "watcher_local_model"])
async def test_a_moved_field_sent_alone_as_null_is_refused_not_a_no_op(app, field) -> None:
    """A null names a moved field as surely as a value: the 422 says where it went (docs/api.md), where the field
    was otherwise dropped and the request answered 200 with nothing changed."""
    resp = await _put(app, {field: None})
    assert resp.status_code == 422, f"{field}: null was taken as a no-op: {resp.status_code} {resp.text}"
    assert "moved to the settings document" in resp.json()["detail"], resp.text
    app.state.provider_repo.update.assert_not_awaited()


async def test_a_field_still_here_takes_null_as_leave_it(app) -> None:
    """Only the moved fields are refused for a null: the others keep null as "leave it as stored"."""
    resp = await _put(app, {"fallback_models": None, "default_model": None})
    assert resp.status_code == 200, resp.text
    app.state.provider_repo.update.assert_not_awaited()
