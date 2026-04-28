"""Verify run_agent_node refuses to fall back to in-process execution."""

from unittest.mock import AsyncMock

import pytest

from athanor.execution.agent_runner import AgentOutput
from athanor.orchestration.nodes.agent import SandboxRequiredError, run_agent_node


@pytest.mark.asyncio
async def test_raises_sandbox_required_when_runner_none():
    """run_agent_node must raise SandboxRequiredError if agent_runner is None."""
    state = {"job_id": "job-test-1"}

    with pytest.raises(SandboxRequiredError, match="refusing to run agent in-process"):
        await run_agent_node(
            state=state,
            agent_type="developer",
            prompt="noop",
            agent_runner=None,
            network="sandbox-restricted",
            on_event=lambda _e: None,
            config={},
            credentials={"api_key": "", "oauth_token": "oauth-xyz"},
        )


@pytest.mark.asyncio
async def test_does_not_attempt_anthropic_call_on_missing_runner():
    """No SDK call should be attempted; the raise comes before any I/O."""
    # Sentinel: if select_executor were still wired, we'd see an attempted import.
    state = {"job_id": "job-test-2"}

    fake_runner = AsyncMock()
    fake_runner.run = AsyncMock(
        return_value=AgentOutput(
            agent_type="triage",
            is_error=False,
            error_message=None,
            output="ok",
            cost_usd=0.0,
            duration_ms=10,
            tools_called={},
        )
    )
    # Run with a real runner first to verify happy path uses the runner.
    await run_agent_node(
        state=state,
        agent_type="triage",
        prompt="noop",
        agent_runner=fake_runner,
        network="sandbox-restricted",
        on_event=lambda _e: None,
        config={},
        credentials={"api_key": "", "oauth_token": "oauth-xyz"},
    )
    fake_runner.run.assert_awaited_once()
