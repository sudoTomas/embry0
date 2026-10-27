"""EMB-36 model→provider routing."""

from unittest.mock import patch

import pytest

from embry0.agents.providers import XAI, provider_for_model
from embry0.safety.policy import default_policy_for_agent


def test_grok_models_route_to_xai():
    assert provider_for_model("grok-4.5") is XAI
    assert provider_for_model("grok-5-mini") is XAI


def test_claude_models_route_to_anthropic_default():
    assert provider_for_model("claude-sonnet-4-6") is None
    assert provider_for_model("claude-opus-4-6") is None


def test_xai_provider_shape():
    assert XAI.base_url == "https://api.x.ai"
    assert XAI.api_key_env == "XAI_API_KEY"
    assert "grok-4.5" in XAI.models
    assert XAI.pricing_usd_per_mtok["grok-4.5"] == (2.0, 6.0)


def test_grok_4_7_is_in_the_xai_catalogue_with_its_price():
    """#465: routing by the ``grok-`` prefix is not enough — an unlisted id is
    missing from GET /config/models and from known_models()."""
    from embry0.agents.providers import known_models

    assert "grok-4.7" in XAI.models, f"grok-4.7 missing from the xAI catalogue: {XAI.models}"
    assert "grok-4.7" in known_models()
    assert XAI.pricing_usd_per_mtok.get("grok-4.7") == (2.0, 6.0)
    assert "grok-4.5" in XAI.models, "grok-4.5 stays in the catalogue"


def test_direct_executor_prices_grok_4_7():
    """#465: an unpriced id is recorded as free by the direct xAI executor."""
    from embry0.agents.executor_xai import _compute_cost

    cost = _compute_cost("grok-4.7", 1_000_000, 1_000_000)
    assert cost == pytest.approx(8.0), f"1M in + 1M out on grok-4.7 is $2 + $6 = $8.00, got ${cost}"


def test_every_catalogue_model_is_priced():
    """A model listed without a price would cost $0 on the direct executor."""
    from embry0.agents.providers import CATALOG_PROVIDERS

    unpriced = [m for p in CATALOG_PROVIDERS for m in p.models if m not in p.pricing_usd_per_mtok]
    assert unpriced == [], f"catalogue models without a price: {unpriced}"


def test_resolver_stamps_provider_for_grok_model():
    from embry0.agents.resolver import resolve_agent_invocation

    inv = resolve_agent_invocation(
        agent_type="developer",
        prompt="p",
        system_context="",
        global_defaults={"execution_mode": "sdk", "auth_mode": "oauth"},
        repo_prefs=None,
        job_overrides={},
        agent_definition={
            "model": "grok-4.5",
            "tools": [],
            "skills": [],
            "system_prompt": "",
            "mcp_servers": {},
            "execution_mode": None,
            "auth_mode": None,
        },
        pipeline_config={},
        max_turns=5,
        timeout_seconds=60,
        credentials={"api_key": "", "oauth_token": "tok"},
    )
    assert inv.model == "grok-4.5"
    assert inv.provider == "xai"


def test_resolver_no_provider_for_claude():
    from embry0.agents.resolver import resolve_agent_invocation

    inv = resolve_agent_invocation(
        agent_type="developer",
        prompt="p",
        system_context="",
        global_defaults={"execution_mode": "sdk", "auth_mode": "oauth"},
        repo_prefs=None,
        job_overrides={},
        agent_definition={
            "model": "claude-sonnet-4-6",
            "tools": [],
            "skills": [],
            "system_prompt": "",
            "mcp_servers": {},
            "execution_mode": None,
            "auth_mode": None,
        },
        pipeline_config={},
        max_turns=5,
        timeout_seconds=60,
        credentials={"api_key": "", "oauth_token": "tok"},
    )
    assert inv.provider is None


# ---------------------------------------------------------------------------
# Executor overlay
# ---------------------------------------------------------------------------


def _inv(**kw):
    from embry0.agents.invocation import AgentInvocation

    base = {
        "agent_type": "developer",
        "prompt": "go",
        "system_prompt": "",
        "system_context": "",
        "model": "grok-4.5",
        "tools": ["Read"],
        "skills": [],
        "mcp_servers": {},
        "max_turns": 5,
        "timeout_seconds": 60,
        "execution_mode": "sdk",
        "auth_mode": "api_key",
        "safety_policy": default_policy_for_agent("developer"),
        "channel_config": None,
        "provider": "xai",
    }
    base.update(kw)
    return AgentInvocation(**base)


class _ResultMessage:
    def __init__(self) -> None:
        self.result = "done"
        self.total_cost_usd = 0.001
        self.duration_ms = 10
        self.num_turns = 1
        self.usage = {"input_tokens": 1, "output_tokens": 1}


async def _one_result(*_a, **_kw):
    yield _ResultMessage()


@pytest.mark.asyncio
async def test_executor_overlays_provider_env(tmp_path, monkeypatch):
    from embry0.agents.executor import SdkAgentExecutor

    monkeypatch.setenv("EMBRY0_WORKSPACE_ROOT", str(tmp_path))
    monkeypatch.setenv("XAI_API_KEY", "xai-sekrit")
    captured = {}

    def fake_query(*, prompt, options):
        captured["options"] = options
        return _one_result()

    with patch("claude_agent_sdk.query", side_effect=fake_query):
        out = await SdkAgentExecutor().run(
            _inv(),
            config={"configurable": {}, "_test_writer": lambda _e: None},
        )

    assert out.is_error is False
    env = captured["options"].env
    assert env["ANTHROPIC_BASE_URL"] == "https://api.x.ai"
    assert env["ANTHROPIC_API_KEY"] == "xai-sekrit"
    assert env["CLAUDE_CODE_OAUTH_TOKEN"] == ""


@pytest.mark.asyncio
async def test_executor_fails_closed_without_provider_key(tmp_path, monkeypatch):
    from embry0.agents.executor import SdkAgentExecutor

    monkeypatch.setenv("EMBRY0_WORKSPACE_ROOT", str(tmp_path))
    monkeypatch.delenv("XAI_API_KEY", raising=False)

    out = await SdkAgentExecutor().run(
        _inv(),
        config={"configurable": {}, "_test_writer": lambda _e: None},
    )
    assert out.is_error is True
    assert "XAI_API_KEY" in out.error_message


@pytest.mark.asyncio
async def test_executor_no_overlay_for_anthropic(tmp_path, monkeypatch):
    from embry0.agents.executor import SdkAgentExecutor

    monkeypatch.setenv("EMBRY0_WORKSPACE_ROOT", str(tmp_path))
    captured = {}

    def fake_query(*, prompt, options):
        captured["options"] = options
        return _one_result()

    with patch("claude_agent_sdk.query", side_effect=fake_query):
        await SdkAgentExecutor().run(
            _inv(model="claude-sonnet-4-6", provider=None),
            config={"configurable": {}, "_test_writer": lambda _e: None},
        )
        # #240: every SDK agent carries the tool-search kill-switch; the assertion
        # here is that NO PROVIDER key rides along for anthropic models.
        assert getattr(captured["options"], "env", None) == {"ENABLE_TOOL_SEARCH": "false"}


def test_price_usage_prices_input_output_and_cache_for_a_catalog_model() -> None:
    from embry0.agents.providers import price_usage

    # claude-sonnet-5: $3 in, $15 out per Mtok; cache read 10% of input, cache write 125% of input.
    cost = price_usage(
        "claude-sonnet-5",
        input_tokens=1_000_000,
        output_tokens=1_000_000,
        cache_read_tokens=1_000_000,
        cache_creation_tokens=1_000_000,
    )
    assert cost == pytest.approx(3.0 + 15.0 + 0.3 + 3.75)


def test_price_usage_is_none_for_an_unpriced_model() -> None:
    from embry0.agents.providers import price_usage

    assert price_usage("grok-9-unlisted", input_tokens=10, output_tokens=10) is None, (
        "an unpriced model must not read as free"
    )


def test_a_local_model_server_gets_the_slow_provider_budgets() -> None:
    """A model served on the operator's own card is slower per call than a hosted one: the
    agents' Claude-sized turn and time budgets double, as grok-4.7's do."""
    from embry0.agents.providers import CUSTOM

    assert CUSTOM.timeout_factor == XAI.timeout_factor == 2.0
    assert CUSTOM.turn_budget_factor == XAI.turn_budget_factor == 2.0
