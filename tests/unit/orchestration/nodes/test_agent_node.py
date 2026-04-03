from unittest.mock import AsyncMock, MagicMock

import pytest

from legion.execution.agent_runner import AgentOutput
from legion.orchestration.nodes.agent import run_agent_node


@pytest.mark.asyncio
async def test_run_agent_node_success():
    mock_runner = MagicMock()
    mock_runner.run = AsyncMock(
        return_value=AgentOutput(
            agent_type="developer",
            is_error=False,
            output="Fixed the bug",
            cost_usd=0.42,
            duration_ms=15000,
            tools_called={"Read": 5, "Edit": 2},
        )
    )
    state = {"sandbox_container_id": "sandbox-job-123", "total_cost_usd": 1.0}
    result = await run_agent_node(
        state=state,
        agent_runner=mock_runner,
        agent_type="developer",
        prompt="Fix the auth bug",
        model="claude-sonnet-4-6",
        tools=["Read", "Write", "Edit", "Bash"],
    )
    assert len(result["agent_outputs"]) == 1
    assert result["agent_outputs"][0]["agent_type"] == "developer"
    assert result["agent_outputs"][0]["cost_usd"] == 0.42
    assert result["total_cost_usd"] == 1.42
    assert result["current_stage"] == "developer_complete"


@pytest.mark.asyncio
async def test_run_agent_node_error():
    mock_runner = MagicMock()
    mock_runner.run = AsyncMock(
        return_value=AgentOutput(
            agent_type="developer",
            is_error=True,
            error_message="Timeout after 300s",
        )
    )
    state = {"sandbox_container_id": "sandbox-job-123", "total_cost_usd": 0.0}
    result = await run_agent_node(
        state=state, agent_runner=mock_runner, agent_type="developer", prompt="Fix the bug",
    )
    assert result["agent_outputs"][0]["is_error"] is True
    assert len(result["errors"]) == 1
    assert "Timeout" in result["errors"][0]


@pytest.mark.asyncio
async def test_run_agent_node_with_network():
    mock_runner = MagicMock()
    mock_runner.run = AsyncMock(
        return_value=AgentOutput(agent_type="researcher", output="Found data")
    )
    state = {"sandbox_container_id": "sandbox-job-123", "total_cost_usd": 0.0}
    await run_agent_node(
        state=state, agent_runner=mock_runner, agent_type="researcher",
        prompt="Research topic", network="sandbox-internet",
    )
    call_kwargs = mock_runner.run.call_args.kwargs
    assert call_kwargs["network"] == "sandbox-internet"
