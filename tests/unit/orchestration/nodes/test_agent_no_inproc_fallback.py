"""Verify run_agent_node refuses to fall back to in-process execution."""

from unittest.mock import AsyncMock, MagicMock

import pytest

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
async def test_no_runner_call_attempted_when_runner_none():
    """Verify the raise short-circuits before any I/O is attempted.

    We pass agent_runner=None and also wire up a poison runner that would
    explode if somehow reached. The test passes only if the SandboxRequiredError
    is raised before any runner.run call occurs.
    """
    state = {"job_id": "job-test-2"}

    poison_runner = MagicMock()
    poison_runner.run = AsyncMock(
        side_effect=AssertionError("must not be called when runner is None")
    )

    with pytest.raises(SandboxRequiredError):
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

    # If we reach here, the poison runner was never called.
    poison_runner.run.assert_not_awaited()
