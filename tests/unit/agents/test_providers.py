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
    assert getattr(captured["options"], "env", None) in (None, {})
