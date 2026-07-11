"""Matrix smoke tests — one cell per test. Phase 1: sdk+api_key only.

Phase 2 unblocks the other three cells."""

import pytest


@pytest.mark.sandbox_smoke
@pytest.mark.asyncio
async def test_sdk_api_key(smoke_api_key) -> None:
    """End-to-end: sdk execution_mode + api_key auth_mode against real Anthropic API."""
    pytest.importorskip("claude_agent_sdk")

    from embry0.agents.executor_factory import select_executor
    from embry0.agents.invocation import AgentInvocation
    from embry0.safety.policy import default_policy_for_agent

    invocation = AgentInvocation(
        agent_type="developer",
        prompt="Please respond with exactly the word 'hello' and nothing else.",
        system_prompt="You are a terse assistant. Never add extra text.",
        system_context="",
        model="claude-haiku-4-5",
        tools=[],
        skills=[],
        mcp_servers={},
        max_turns=2,
        timeout_seconds=60,
        execution_mode="sdk",
        auth_mode="api_key",
        safety_policy=default_policy_for_agent("developer"),
        channel_config=None,
    )

    import os

    os.environ["ANTHROPIC_API_KEY"] = smoke_api_key

    executor = select_executor(invocation)
    events: list[dict] = []
    result = await executor.run(
        invocation,
        config={"configurable": {}, "_test_writer": lambda e: events.append(e)},
    )

    assert result.is_error is False, f"smoke failed: {result.error_message}"
    assert "hello" in result.output.lower()
    assert result.cost_usd > 0.0
    assert any(e["type"] in {"text", "agent_completed"} for e in events)


@pytest.mark.sandbox_smoke
@pytest.mark.skip(reason="sdk+oauth smoke added in Phase 2 alongside cli implementations")
async def test_sdk_oauth() -> None:
    pass


@pytest.mark.sandbox_smoke
@pytest.mark.skip(reason="cli+api_key lands in Phase 2")
async def test_cli_api_key() -> None:
    pass


@pytest.mark.sandbox_smoke
@pytest.mark.skip(reason="cli+oauth lands in Phase 2")
async def test_cli_oauth() -> None:
    pass
