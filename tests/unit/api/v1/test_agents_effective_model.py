"""GET /agents says which model and provider each agent actually runs on.

The Agents page showed each agent's stored model, a Claude model on
Anthropic, while every job ran the deployment's default model on xAI: a
default model replaces the stored one at run time (#465). The API now
reports the effective model and provider next to the stored fields,
resolved by the code the executor runs, and GET /config/models marks the
provider that serves the default model as the default provider.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from httpx import ASGITransport, AsyncClient

from embry0.api.app import create_app
from embry0.config import Embry0Config
from embry0.workflows.pr_review.agent_seed import PR_REVIEWER_AGENT_SEED

DEVELOPER = {
    "type": "developer",
    "description": "Writes and edits code",
    "model": "claude-opus-5",
    "tools": ["Read", "Edit"],
    "skills": [],
    "system_prompt": "",
    "is_builtin": True,
}
PR_REVIEWER = {"type": "pr-reviewer", **PR_REVIEWER_AGENT_SEED, "is_builtin": True}
TIERS = {"model_heavy": "claude-opus-5", "model_medium": "claude-sonnet-5", "model_light": "claude-haiku-4-5"}


def _app(provider_config: dict[str, Any] | Exception, agents: tuple[dict[str, Any], ...] = (DEVELOPER,)) -> Any:
    config = Embry0Config(_env_file=None, auth_dev_mode=True, webhook_dev_mode=True)
    app = create_app(config)
    repo = MagicMock()
    repo.list_all = AsyncMock(return_value=list(agents))
    repo.get = AsyncMock(side_effect=lambda agent_type: next((a for a in agents if a["type"] == agent_type), None))
    app.state.agent_defs_repo = repo
    provider_repo = MagicMock()
    if isinstance(provider_config, Exception):
        provider_repo.get = AsyncMock(side_effect=provider_config)
    else:
        provider_repo.get = AsyncMock(return_value={**TIERS, "disabled_models": [], **provider_config})
    app.state.provider_repo = provider_repo
    app.state.xai_refresher = None
    return app


@pytest.fixture(autouse=True)
def _both_providers_configured(monkeypatch):
    monkeypatch.setenv("CLAUDE_CODE_OAUTH_TOKEN", "tok-not-real")
    monkeypatch.setenv("XAI_API_KEY", "key-not-real")


async def _get(app: Any, path: str) -> Any:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get(path)
    assert resp.status_code == 200, resp.text
    return resp.json()


def _effective(agent: dict[str, Any]) -> tuple[Any, ...]:
    return tuple(
        agent.get(key)
        for key in ("effective_model", "effective_provider", "effective_provider_ready", "effective_model_source")
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("path", ["/api/v1/agents", "/api/v1/agents/developer"])
async def test_a_default_model_is_what_the_agent_runs_on(path: str) -> None:
    data = await _get(_app({"default_model": "grok-4.7"}), path)
    agent = data[0] if isinstance(data, list) else data
    # The stored fields are unchanged ...
    assert (agent["model"], agent["provider"], agent["provider_ready"]) == ("claude-opus-5", "anthropic", True)
    # ... and next to them, what a job runs.
    assert _effective(agent) == ("grok-4.7", "xai", True, "default_model"), (
        f"GET {path} does not say the developer runs on the default model: {_effective(agent)}"
    )


@pytest.mark.asyncio
async def test_without_a_default_the_agent_runs_its_own_model() -> None:
    [agent] = await _get(_app({"default_model": ""}), "/api/v1/agents")
    assert _effective(agent) == ("claude-opus-5", "anthropic", True, "agent")


@pytest.mark.asyncio
async def test_a_disabled_model_runs_the_tier_fallback() -> None:
    [agent] = await _get(_app({"default_model": "", "disabled_models": ["claude-opus-5"]}), "/api/v1/agents")
    # The developer falls back to the heavy tier, which here is the disabled model itself:
    # no runnable fallback, so the pinned model runs anyway.
    assert _effective(agent)[0] == "claude-opus-5"
    app = _app({"default_model": "", "disabled_models": ["claude-opus-5"], "model_heavy": "claude-sonnet-5"})
    [agent] = await _get(app, "/api/v1/agents")
    assert _effective(agent) == ("claude-sonnet-5", "anthropic", True, "disabled_fallback")


@pytest.mark.asyncio
async def test_an_unreadable_provider_config_reports_the_stored_model() -> None:
    """The executor treats a failed read as no policy; so does the list."""
    [agent] = await _get(_app(RuntimeError("db down")), "/api/v1/agents")
    assert _effective(agent) == ("claude-opus-5", "anthropic", True, "agent")


@pytest.mark.asyncio
@pytest.mark.parametrize("path", ["/api/v1/agents", "/api/v1/agents/pr-reviewer"])
async def test_a_default_model_leaves_the_pr_reviewer_on_the_heavy_tier(path: str) -> None:
    """Every pr-review job names the heavy tier, so a default model never moves the reviewer."""
    data = await _get(_app({"default_model": "grok-4.7"}, (DEVELOPER, PR_REVIEWER)), path)
    reviewer = next(a for a in data if a["type"] == "pr-reviewer") if isinstance(data, list) else data
    assert _effective(reviewer) == ("claude-opus-5", "anthropic", True, "per_job_heavy_tier"), (
        f"GET {path} does not say the pr-reviewer runs on the heavy tier: {_effective(reviewer)}"
    )
    if isinstance(data, list):
        developer = next(a for a in data if a["type"] == "developer")
        assert _effective(developer) == ("grok-4.7", "xai", True, "default_model")


@pytest.mark.asyncio
async def test_the_pr_reviewer_reports_the_heavy_tier_provider() -> None:
    """The heavy tier, not the stored model, names the reviewer's provider."""
    app = _app({"default_model": "", "model_heavy": "grok-4.7"}, (PR_REVIEWER,))
    [reviewer] = await _get(app, "/api/v1/agents")
    assert reviewer["model"] == "claude-opus-5"
    assert _effective(reviewer) == ("grok-4.7", "xai", True, "per_job_heavy_tier")


@pytest.mark.asyncio
async def test_the_default_provider_is_the_one_serving_the_default_model() -> None:
    data = await _get(_app({"default_model": "grok-4.7"}), "/api/v1/config/models")
    defaults = {p["name"]: p["default"] for p in data["providers"]}
    assert defaults == {"anthropic": False, "xai": True}, f"GET /config/models marks {defaults}"


@pytest.mark.asyncio
async def test_without_a_default_model_anthropic_stays_the_default_provider() -> None:
    data = await _get(_app({"default_model": ""}), "/api/v1/config/models")
    assert {p["name"]: p["default"] for p in data["providers"]} == {"anthropic": True, "xai": False}
