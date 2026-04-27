"""executor_factory.select_executor — dispatch and phase-gating."""

import pytest

from athanor.agents.executor_factory import select_executor
from athanor.agents.invocation import AgentInvocation
from athanor.execution.auth_provider import AuthConfigError
from athanor.safety.error_codes import ErrorCode
from athanor.safety.policy import default_policy_for_agent


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
    from athanor.agents.executor import SdkAgentExecutor

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
