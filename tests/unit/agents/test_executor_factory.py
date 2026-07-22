"""executor_factory.select_executor — dispatch and phase-gating."""

import pytest

from embry0.agents.executor_factory import select_executor
from embry0.agents.invocation import AgentInvocation
from embry0.execution.auth_provider import AuthConfigError
from embry0.safety.error_codes import ErrorCode
from embry0.safety.policy import default_policy_for_agent


def _inv(execution_mode: str) -> AgentInvocation:
    return AgentInvocation(
        agent_type="developer",
        prompt="x",
        system_prompt="",
        system_context="",
        model="claude-sonnet-4-6",
        tools=["Read"],
        skills=[],
        mcp_servers={},
        max_turns=5,
        timeout_seconds=60,
        execution_mode=execution_mode,  # type: ignore[arg-type]
        auth_mode="api_key",
        safety_policy=default_policy_for_agent("developer"),
        channel_config=None,
    )


def test_select_executor_returns_sdk_for_sdk_mode() -> None:
    from embry0.agents.executor import SdkAgentExecutor

    executor = select_executor(_inv("sdk"))
    assert isinstance(executor, SdkAgentExecutor)


def test_select_executor_rejects_cli_in_phase1() -> None:
    with pytest.raises(AuthConfigError) as exc:
        select_executor(_inv("cli"))
    assert exc.value.error_code == ErrorCode.INVALID_CONFIG
    assert "cli" in str(exc.value).lower()


def test_select_executor_rejects_unknown_mode() -> None:
    with pytest.raises(AuthConfigError) as exc:
        select_executor(_inv("bogus"))
    assert exc.value.error_code == ErrorCode.INVALID_CONFIG


# ---------------------------------------------------------------------------
# EMB-46: grok routing — SDK-over-proxy default, DirectXaiExecutor opt-in
# ---------------------------------------------------------------------------


def _xai_inv() -> AgentInvocation:
    return AgentInvocation(
        agent_type="qa",
        prompt="x",
        system_prompt="",
        system_context="",
        model="grok-4.5",
        tools=["Read"],
        skills=[],
        mcp_servers={},
        max_turns=5,
        timeout_seconds=60,
        execution_mode="sdk",
        auth_mode="oauth",
        safety_policy=default_policy_for_agent("qa"),
        channel_config=None,
        provider="xai",
    )


def test_xai_with_proxy_defaults_to_sdk(monkeypatch) -> None:
    """EMB-46: proxy live but no opt-in flag → grok runs via the Agent SDK."""
    from embry0.agents.executor import SdkAgentExecutor

    monkeypatch.setenv("EMBRY0_XAI_PROXY_URL", "http://xai-proxy:9106")
    monkeypatch.delenv("EMBRY0_XAI_DIRECT_EXECUTOR", raising=False)
    assert isinstance(select_executor(_xai_inv()), SdkAgentExecutor)


def test_xai_direct_executor_requires_opt_in_flag(monkeypatch) -> None:
    from embry0.agents.executor_xai import DirectXaiExecutor

    monkeypatch.setenv("EMBRY0_XAI_PROXY_URL", "http://xai-proxy:9106")
    monkeypatch.setenv("EMBRY0_XAI_DIRECT_EXECUTOR", "true")
    assert isinstance(select_executor(_xai_inv()), DirectXaiExecutor)


def test_xai_without_proxy_uses_sdk_even_with_flag(monkeypatch) -> None:
    """The direct executor cannot run without the proxy — flag alone is not enough."""
    from embry0.agents.executor import SdkAgentExecutor

    monkeypatch.delenv("EMBRY0_XAI_PROXY_URL", raising=False)
    monkeypatch.setenv("EMBRY0_XAI_DIRECT_EXECUTOR", "true")
    assert isinstance(select_executor(_xai_inv()), SdkAgentExecutor)
