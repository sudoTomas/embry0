"""Provider readiness: model to provider, provider to credential, before dispatch."""

from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from embry0.agents.providers import ANTHROPIC, CATALOG_PROVIDERS, XAI, provider_for_model
from embry0.services.provider_readiness import (
    PROBE_AUDIT_ACTION,
    ProbeResult,
    ProviderNotReadyError,
    ProviderReadiness,
    annotate_agent,
    annotate_agents,
    catalog_provider_for_model,
    ensure_model_provider_ready,
    last_probe,
    provider_availability,
    readiness_by_provider,
)
from embry0.storage.repositories.agent_definitions import BUILTIN_SEED
from embry0.workflows.qa.agent_seed import QA_AGENT_SEED

# Every agent embry0 seeds itself: BUILTIN_SEED plus the qa agent, which has its own seeder.
BUILTIN_AGENTS = {**BUILTIN_SEED, "qa": QA_AGENT_SEED}
CATALOG_MODELS = [(provider, model) for provider in CATALOG_PROVIDERS for model in provider.models]
PROBED_AT = datetime(2026, 1, 2, 3, 4, 5, tzinfo=UTC)


@pytest.fixture(autouse=True)
def _no_provider_env(monkeypatch):
    for key in ("CLAUDE_CODE_OAUTH_TOKEN", "ANTHROPIC_API_KEY", "XAI_API_KEY"):
        monkeypatch.delenv(key, raising=False)


def _state(**attrs):
    return SimpleNamespace(**attrs)


# ---------------------------------------------------------------- resolver


def test_claude_models_resolve_to_anthropic():
    assert catalog_provider_for_model("claude-sonnet-5") is ANTHROPIC
    assert catalog_provider_for_model("claude-opus-4-6") is ANTHROPIC


def test_grok_models_resolve_to_xai():
    assert catalog_provider_for_model("grok-4.5") is XAI
    assert catalog_provider_for_model("grok-5-mini") is XAI


def test_unknown_prefix_and_empty_model_resolve_to_no_provider():
    assert catalog_provider_for_model("gpt-4o") is None
    assert catalog_provider_for_model("") is None


# ------------------------------------------------------------ availability


@pytest.mark.asyncio
async def test_anthropic_ready_from_oauth_env(monkeypatch):
    monkeypatch.setenv("CLAUDE_CODE_OAUTH_TOKEN", "tok-not-real")
    assert await provider_availability(_state(), ANTHROPIC) == (True, "oauth")


@pytest.mark.asyncio
async def test_anthropic_ready_from_both_env_sources(monkeypatch):
    monkeypatch.setenv("CLAUDE_CODE_OAUTH_TOKEN", "tok-not-real")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "key-not-real")
    assert await provider_availability(_state(), ANTHROPIC) == (True, "oauth+api_key")


@pytest.mark.asyncio
async def test_anthropic_not_ready_without_any_credential():
    assert await provider_availability(_state(), ANTHROPIC) == (False, None)


@pytest.mark.asyncio
async def test_xai_ready_from_refresher_without_key():
    assert await provider_availability(_state(xai_refresher=MagicMock()), XAI) == (True, "oauth_proxy")


@pytest.mark.asyncio
async def test_xai_ready_from_api_key_env(monkeypatch):
    monkeypatch.setenv("XAI_API_KEY", "key-not-real")
    assert await provider_availability(_state(xai_refresher=None), XAI) == (True, "api_key")


@pytest.mark.asyncio
async def test_registry_row_counts_only_when_env_is_empty():
    repo = MagicMock()
    repo.any_for_provider = AsyncMock(side_effect=lambda provider: provider == "xai")
    state = _state(credentials_repo=repo, xai_refresher=None)
    assert await provider_availability(state, XAI) == (True, "registry")
    assert await provider_availability(state, ANTHROPIC) == (False, None)


@pytest.mark.asyncio
async def test_readiness_by_provider_covers_the_catalog(monkeypatch):
    monkeypatch.setenv("CLAUDE_CODE_OAUTH_TOKEN", "tok-not-real")
    readiness = await readiness_by_provider(_state(xai_refresher=None))
    assert readiness["anthropic"] == ProviderReadiness("anthropic", True, "oauth")
    assert readiness["xai"] == ProviderReadiness("xai", False, None)


# ---------------------------------------------------------------- annotate


def test_annotate_agent_names_provider_and_readiness():
    readiness = {
        "anthropic": ProviderReadiness("anthropic", True, "oauth"),
        "xai": ProviderReadiness("xai", False, None),
    }
    assert annotate_agent({"type": "developer", "model": "claude-opus-5"}, readiness) == {
        "type": "developer",
        "model": "claude-opus-5",
        "provider": "anthropic",
        "provider_ready": True,
    }
    grok = annotate_agent({"type": "grok-dev", "model": "grok-4.5"}, readiness)
    assert (grok["provider"], grok["provider_ready"]) == ("xai", False)


def test_annotate_agent_unknown_model_has_no_provider():
    out = annotate_agent({"type": "x", "model": "gpt-4o"}, {})
    assert out["provider"] is None
    assert out["provider_ready"] is False


@pytest.mark.asyncio
async def test_annotate_agents_keeps_every_existing_field(monkeypatch):
    monkeypatch.setenv("XAI_API_KEY", "key-not-real")
    rows = [{"type": "a", "model": "grok-4.7", "tools": ["Read"], "is_builtin": False}]
    [out] = await annotate_agents(rows, _state(xai_refresher=None))
    assert out["tools"] == ["Read"]
    assert out["is_builtin"] is False
    assert (out["provider"], out["provider_ready"]) == ("xai", True)


# ---------------------------------------------------------------- the gate


@pytest.mark.asyncio
async def test_ensure_ready_passes_for_a_ready_provider(monkeypatch):
    monkeypatch.setenv("XAI_API_KEY", "key-not-real")
    await ensure_model_provider_ready("grok-4.5", _state(xai_refresher=None))


@pytest.mark.asyncio
async def test_ensure_ready_refuses_naming_the_missing_provider():
    with pytest.raises(ProviderNotReadyError) as exc_info:
        await ensure_model_provider_ready("grok-4.5", _state(xai_refresher=None))
    message = str(exc_info.value)
    assert "'xai'" in message
    assert "'grok-4.5'" in message
    assert "no credential" in message


@pytest.mark.asyncio
async def test_ensure_ready_refuses_a_model_no_provider_serves():
    with pytest.raises(ProviderNotReadyError) as exc_info:
        await ensure_model_provider_ready("gpt-4o", _state())
    message = str(exc_info.value)
    assert "no configured provider serves model 'gpt-4o'" in message
    assert "anthropic (claude-)" in message
    assert "xai (grok-)" in message


# ------------------------------------- every built-in agent, every model


@pytest.mark.parametrize("agent_type", sorted(BUILTIN_AGENTS))
@pytest.mark.parametrize(("provider", "model"), CATALOG_MODELS, ids=[model for _, model in CATALOG_MODELS])
def test_every_builtin_agent_on_every_catalog_model_names_the_serving_provider(agent_type, provider, model):
    """Any agent can run on any catalogue model; the annotation names the provider that serves it."""
    agent = {"type": agent_type, **BUILTIN_AGENTS[agent_type], "model": model}

    ready = {p.name: ProviderReadiness(p.name, True, "api_key") for p in CATALOG_PROVIDERS}
    out = annotate_agent(agent, ready)
    assert out["provider"] == provider.name
    assert out["provider_ready"] is True

    not_ready = {**ready, provider.name: ProviderReadiness(provider.name, False, None)}
    assert annotate_agent(agent, not_ready)["provider_ready"] is False

    # The runtime routes the same way: a provider overlay for a non-Anthropic model,
    # none (the Anthropic default) for a Claude model.
    routed = provider_for_model(model)
    assert (routed.name if routed is not None else ANTHROPIC.name) == provider.name


@pytest.mark.parametrize("agent_type", sorted(BUILTIN_AGENTS))
def test_every_builtin_agent_seed_model_is_a_catalog_model(agent_type):
    model = BUILTIN_AGENTS[agent_type]["model"]
    provider = catalog_provider_for_model(model)
    assert provider is not None, f"{agent_type} seeds {model!r}, which no catalogue provider serves"
    assert model in provider.models


@pytest.mark.asyncio
@pytest.mark.parametrize(("provider", "model"), CATALOG_MODELS, ids=[model for _, model in CATALOG_MODELS])
async def test_gate_follows_the_serving_provider_for_every_catalog_model(monkeypatch, provider, model):
    monkeypatch.setenv(provider.api_key_env, "key-not-real")
    await ensure_model_provider_ready(model, _state(xai_refresher=None))

    monkeypatch.delenv(provider.api_key_env)
    with pytest.raises(ProviderNotReadyError, match=f"provider {provider.name!r}"):
        await ensure_model_provider_ready(model, _state(xai_refresher=None))


# -------------------------------------------------------------- last probe


def _audit_repo(rows):
    repo = MagicMock()
    repo.latest_entry = AsyncMock(side_effect=lambda action, *, detail_key, detail_value: rows.get(detail_value))
    return repo


@pytest.mark.asyncio
async def test_last_probe_reads_the_newest_connection_test_from_the_audit_log():
    repo = _audit_repo({"xai": {"details": {"provider": "xai", "status": "unauthorized"}, "created_at": PROBED_AT}})
    assert await last_probe(_state(audit_repo=repo), "xai") == ProbeResult("unauthorized", PROBED_AT)
    repo.latest_entry.assert_awaited_once_with(PROBE_AUDIT_ACTION, detail_key="provider", detail_value="xai")


@pytest.mark.asyncio
async def test_last_probe_is_none_without_a_test_or_an_audit_repo():
    assert await last_probe(_state(audit_repo=_audit_repo({})), "anthropic") is None
    assert await last_probe(_state(), "anthropic") is None


@pytest.mark.asyncio
async def test_readiness_reports_credential_and_last_probe_side_by_side(monkeypatch):
    """A configured credential whose last test failed is still ready: the two facts are not merged."""
    monkeypatch.setenv("CLAUDE_CODE_OAUTH_TOKEN", "tok-not-real")
    repo = _audit_repo({"anthropic": {"details": {"status": "unauthorized"}, "created_at": PROBED_AT}})
    readiness = await readiness_by_provider(_state(audit_repo=repo, xai_refresher=None), with_last_probe=True)
    assert readiness["anthropic"] == ProviderReadiness(
        "anthropic", True, "oauth", ProbeResult("unauthorized", PROBED_AT)
    )
    assert readiness["xai"] == ProviderReadiness("xai", False, None, None)


@pytest.mark.asyncio
async def test_readiness_skips_the_audit_read_unless_asked():
    repo = _audit_repo({})
    readiness = await readiness_by_provider(_state(audit_repo=repo, xai_refresher=None))
    assert all(state.last_probe is None for state in readiness.values())
    repo.latest_entry.assert_not_awaited()
