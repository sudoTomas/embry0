"""AgentInvocation frozen dataclass — field shape, immutability, defaults."""

import pytest

from embry0.agents.invocation import AgentInvocation, ChannelConfig
from embry0.safety.policy import SafetyPolicy, default_policy_for_agent


def _minimal_invocation() -> AgentInvocation:
    return AgentInvocation(
        agent_type="developer",
        prompt="hello",
        system_prompt="",
        system_context="",
        model="claude-sonnet-4-6",
        tools=["Read"],
        skills=[],
        mcp_servers={},
        max_turns=10,
        timeout_seconds=60,
        execution_mode="sdk",
        auth_mode="api_key",
        safety_policy=default_policy_for_agent("developer"),
        channel_config=None,
    )


def test_invocation_is_frozen() -> None:
    inv = _minimal_invocation()
    with pytest.raises(Exception):
        inv.model = "other"  # type: ignore[misc]


def test_invocation_carries_all_expected_fields() -> None:
    inv = _minimal_invocation()
    assert inv.agent_type == "developer"
    assert inv.prompt == "hello"
    assert inv.execution_mode == "sdk"
    assert inv.auth_mode == "api_key"
    assert isinstance(inv.safety_policy, SafetyPolicy)
    assert inv.channel_config is None


def test_channel_config_is_frozen() -> None:
    cc = ChannelConfig(enabled=True, platform="telegram", chat_id="12345")
    with pytest.raises(Exception):
        cc.enabled = False  # type: ignore[misc]
    assert cc.chat_id == "12345"


def test_invocation_with_channel_config() -> None:
    cc = ChannelConfig(enabled=True, platform="discord", chat_id="abc")
    inv = AgentInvocation(
        agent_type="developer",
        prompt="p",
        system_prompt="",
        system_context="",
        model="claude-sonnet-4-6",
        tools=[],
        skills=[],
        mcp_servers={},
        max_turns=5,
        timeout_seconds=30,
        execution_mode="cli",
        auth_mode="oauth",
        safety_policy=default_policy_for_agent("developer"),
        channel_config=cc,
    )
    assert inv.channel_config == cc
    assert inv.channel_config.platform == "discord"
