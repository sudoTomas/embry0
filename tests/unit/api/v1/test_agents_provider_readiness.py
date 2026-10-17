"""GET /agents names each agent's provider and its readiness; saving a model whose
provider is not ready is refused only when AGENT_MODEL_PROVIDER_CHECK is on."""

from unittest.mock import AsyncMock, MagicMock

import pytest
from httpx import ASGITransport, AsyncClient

from embry0.api.app import create_app
from embry0.config import Embry0Config

DEVELOPER = {
    "type": "developer",
    "description": "Writes and edits code",
    "model": "claude-opus-5",
    "tools": ["Read", "Edit"],
    "skills": [],
    "system_prompt": "",
    "is_builtin": True,
}
GROK_AGENT = {**DEVELOPER, "type": "grok-dev", "model": "grok-4.5", "is_builtin": False}
CSRF = {"X-Requested-With": "XMLHttpRequest"}


def _app(**config_overrides):
    config = Embry0Config(_env_file=None, auth_dev_mode=True, webhook_dev_mode=True, **config_overrides)
    app = create_app(config)
    repo = MagicMock()
    repo.list_all = AsyncMock(return_value=[DEVELOPER, GROK_AGENT])
    repo.get = AsyncMock(return_value=GROK_AGENT)
    repo.create = AsyncMock(return_value={**GROK_AGENT, "type": "custom"})
    repo.update = AsyncMock(return_value=GROK_AGENT)
    app.state.agent_defs_repo = repo
    app.state.xai_refresher = None
    return app


@pytest.fixture(autouse=True)
def _only_anthropic_configured(monkeypatch):
    monkeypatch.setenv("CLAUDE_CODE_OAUTH_TOKEN", "tok-not-real")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("XAI_API_KEY", raising=False)


def _client(app):
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


# ------------------------------------------------------------- visibility


@pytest.mark.asyncio
async def test_list_agents_carries_provider_and_readiness():
    async with _client(_app()) as client:
        data = (await client.get("/api/v1/agents")).json()
    by_type = {a["type"]: a for a in data}
    assert (by_type["developer"]["provider"], by_type["developer"]["provider_ready"]) == ("anthropic", True)
    assert (by_type["grok-dev"]["provider"], by_type["grok-dev"]["provider_ready"]) == ("xai", False)
    # Every field the row had is still there.
    assert by_type["developer"]["tools"] == ["Read", "Edit"]


@pytest.mark.asyncio
async def test_get_agent_carries_provider_and_readiness():
    app = _app()
    app.state.xai_refresher = MagicMock()
    async with _client(app) as client:
        data = (await client.get("/api/v1/agents/grok-dev")).json()
    assert data["provider"] == "xai"
    assert data["provider_ready"] is True


# ----------------------------------------------------- default: permissive


@pytest.mark.asyncio
async def test_default_saves_a_model_whose_provider_is_not_ready():
    """Today's behavior, pinned: the save succeeds and the job fails when it runs."""
    app = _app()
    assert app.state.config.agent_model_provider_check is False
    async with _client(app) as client:
        resp = await client.put("/api/v1/agents/grok-dev", json={"model": "grok-4.5"}, headers=CSRF)
    assert resp.status_code == 200
    app.state.agent_defs_repo.update.assert_awaited_once()


# ----------------------------------------------------------- check is on


@pytest.mark.asyncio
async def test_check_refuses_update_naming_the_provider():
    app = _app(agent_model_provider_check=True)
    async with _client(app) as client:
        resp = await client.put("/api/v1/agents/grok-dev", json={"model": "grok-4.5"}, headers=CSRF)
    assert resp.status_code == 422
    detail = resp.json()["detail"]
    assert "'xai'" in detail
    assert "'grok-4.5'" in detail
    app.state.agent_defs_repo.update.assert_not_awaited()


@pytest.mark.asyncio
async def test_check_refuses_create_for_a_model_no_provider_serves():
    app = _app(agent_model_provider_check=True)
    async with _client(app) as client:
        resp = await client.post(
            "/api/v1/agents",
            json={"type": "custom", "description": "d", "model": "gpt-4o"},
            headers=CSRF,
        )
    assert resp.status_code == 422
    assert "no configured provider serves model 'gpt-4o'" in resp.json()["detail"]
    app.state.agent_defs_repo.create.assert_not_awaited()


@pytest.mark.asyncio
async def test_check_passes_a_ready_provider():
    app = _app(agent_model_provider_check=True)
    app.state.xai_refresher = MagicMock()
    async with _client(app) as client:
        resp = await client.put("/api/v1/agents/grok-dev", json={"model": "grok-4.5"}, headers=CSRF)
    assert resp.status_code == 200
    app.state.agent_defs_repo.update.assert_awaited_once()


@pytest.mark.asyncio
async def test_check_ignores_updates_that_do_not_touch_the_model():
    app = _app(agent_model_provider_check=True)
    async with _client(app) as client:
        resp = await client.put("/api/v1/agents/grok-dev", json={"description": "renamed"}, headers=CSRF)
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_check_runs_after_the_enablement_gate():
    """A disabled model is still refused as disabled; the readiness message does not replace it."""
    app = _app(agent_model_provider_check=True)
    provider_repo = MagicMock()
    provider_repo.get = AsyncMock(return_value={"disabled_models": ["grok-4.5"]})
    app.state.provider_repo = provider_repo
    async with _client(app) as client:
        resp = await client.put("/api/v1/agents/grok-dev", json={"model": "grok-4.5"}, headers=CSRF)
    assert resp.status_code == 422
    assert "disabled in provider settings" in resp.json()["detail"]
