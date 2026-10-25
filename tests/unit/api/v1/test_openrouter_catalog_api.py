"""GET /config/models, the fallback order and the refresh endpoint with OpenRouter's live model list."""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from httpx import ASGITransport, AsyncClient

from embry0.agents import openrouter_catalog as oc
from embry0.api.app import create_app
from embry0.config import Embry0Config

OPUS = "openrouter/anthropic/claude-opus-5.5"
XHR = {"X-Requested-With": "XMLHttpRequest"}


@pytest.fixture(autouse=True)
def _catalog() -> Iterator[None]:
    opus = oc.CatalogModel(
        model=OPUS,
        name="Anthropic: Claude Opus 5.5",
        input_usd_per_mtok=4.0,
        output_usd_per_mtok=20.0,
        context_length=1000000,
    )
    oc.install(oc.Catalog(models={OPUS: opus}, fetched_at=datetime(2026, 10, 25, tzinfo=UTC)))
    yield
    oc.install(None)


@pytest.fixture
def app(monkeypatch: pytest.MonkeyPatch) -> Any:
    monkeypatch.setenv("OPENAI_COMPATIBLE_PROVIDERS", "openrouter")
    app = create_app(Embry0Config(_env_file=None, auth_dev_mode=True, webhook_dev_mode=True))
    provider_repo = MagicMock()
    provider_repo.get = AsyncMock(
        return_value={
            "model_heavy": "claude-opus-5",
            "model_medium": "claude-sonnet-5",
            "model_light": "claude-haiku-4-5",
            "disabled_models": [],
            "fallback_models": [],
        }
    )
    provider_repo.update = AsyncMock(return_value={})
    app.state.provider_repo = provider_repo
    return app


async def _call(app: Any, method: str, path: str, **kw: Any):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        return await client.request(method, f"/api/v1{path}", headers=XHR, **kw)


async def test_the_models_catalog_lists_the_live_models_with_their_price_and_name(app) -> None:
    service = MagicMock()
    service.status.return_value = {"source": "live", "models": 1, "fetched_at": "2026-10-25T00:00:00+00:00"}
    app.state.openrouter_catalog = service

    data = (await _call(app, "GET", "/config/models")).json()
    openrouter = next(p for p in data["providers"] if p["name"] == "openrouter")
    assert [m["model"] for m in openrouter["models"]] == [OPUS], "the live list replaces the static entry"
    (opus,) = openrouter["models"]
    assert opus["pricing_usd_per_mtok"] == {"input": 4.0, "output": 20.0}
    assert (opus["name"], opus["vendor"], opus["context_length"]) == (
        "Anthropic: Claude Opus 5.5",
        "anthropic",
        1000000,
    )
    assert openrouter["catalog"]["source"] == "live"
    anthropic = next(p for p in data["providers"] if p["name"] == "anthropic")
    assert "catalog" not in anthropic and "name" not in anthropic["models"][0]


async def test_without_the_service_the_list_says_it_is_the_static_entry(app) -> None:
    app.state.openrouter_catalog = None
    data = (await _call(app, "GET", "/config/models")).json()
    openrouter = next(p for p in data["providers"] if p["name"] == "openrouter")
    assert openrouter["catalog"] == {"source": "static"}


async def test_a_live_claude_can_stand_in_the_fallback_order_and_an_unlisted_one_cannot(app) -> None:
    ok = await _call(app, "PUT", "/config/provider", json={"fallback_models": ["grok-4.7", OPUS]})
    assert ok.status_code == 200, ok.text
    app.state.provider_repo.update.assert_awaited_once_with(fallback_models=["grok-4.7", OPUS])

    refused = await _call(app, "PUT", "/config/provider", json={"fallback_models": ["openrouter/vendor/not-listed"]})
    assert refused.status_code == 422
    assert "unknown" in refused.json()["detail"]


async def test_reading_the_list_now_returns_its_state(app) -> None:
    service = MagicMock()
    service.refresh = AsyncMock(return_value={"source": "live", "models": 380, "last_error": None})
    app.state.openrouter_catalog = service

    resp = await _call(app, "POST", "/config/providers/openrouter/catalog/refresh")
    assert resp.status_code == 200, resp.text
    assert resp.json()["models"] == 380
    service.refresh.assert_awaited_once()


async def test_reading_the_list_needs_the_service(app) -> None:
    app.state.openrouter_catalog = None
    resp = await _call(app, "POST", "/config/providers/openrouter/catalog/refresh")
    assert resp.status_code == 503
