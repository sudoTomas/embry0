"""Unit tests for qa_node — happy path with mocks."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest


@pytest.mark.asyncio
async def test_qa_node_invokes_run_agent_node():
    """qa_node calls run_agent_node with agent_type='qa'."""
    from athanor.workflows.qa.nodes import qa_node

    state = {
        "job_id": "JOB1",
        "repo": "x/y",
        "qa": {
            "attempts": [{
                "attempt_n": 1,
                "sandbox_id": "container-id-xyz",
                "artifact_prefix": "JOB1/1/",
            }],
            "qa_yaml_parsed": {"mode": "dind"},
            "acceptance_criteria": ["home loads"],
            "sandbox_token": "tok",
        },
    }

    agent_runner = MagicMock()  # opaque object, run_agent_node consumes it
    config = {"configurable": {"agent_runner": agent_runner}}

    fake_run = AsyncMock(return_value={
        "agent_outputs": [{
            "agent_type": "qa",
            "is_error": False,
            "output": "ok",
            "cost_usd": 0.1,
            "duration_ms": 5000,
        }],
    })

    with patch("athanor.orchestration.nodes.agent.run_agent_node", fake_run):
        new_state = await qa_node(state, config)

    fake_run.assert_awaited_once()
    call_kwargs = fake_run.call_args.kwargs
    assert call_kwargs["agent_type"] == "qa"
    assert call_kwargs["agent_runner"] is agent_runner
    assert "Read /workspace/.qa/job.json" in call_kwargs["prompt"]

    last = new_state["qa"]["attempts"][-1]
    assert last["last_phase"] == "report"
    assert last.get("exit_reason") is None


@pytest.mark.asyncio
async def test_qa_node_records_agent_error():
    """If run_agent_node returns an error output, record it on the attempt."""
    from athanor.workflows.qa.nodes import qa_node

    state = {
        "job_id": "JOB",
        "qa": {
            "attempts": [{"attempt_n": 1, "sandbox_id": "C", "artifact_prefix": "JOB/1/"}],
            "qa_yaml_parsed": {}, "acceptance_criteria": [], "sandbox_token": "t",
        },
    }
    agent_runner = MagicMock()
    config = {"configurable": {"agent_runner": agent_runner}}

    fake_run = AsyncMock(return_value={
        "agent_outputs": [{
            "agent_type": "qa",
            "is_error": True,
            "error_message": "executor crashed: connection refused",
        }],
    })

    with patch("athanor.orchestration.nodes.agent.run_agent_node", fake_run):
        new_state = await qa_node(state, config)

    last = new_state["qa"]["attempts"][-1]
    assert last["last_phase"] is None
    assert "agent_error" in last["exit_reason"]
    assert "connection refused" in last["exit_reason"]


@pytest.mark.asyncio
async def test_qa_node_raises_without_agent_runner():
    """No agent_runner in config — fail loudly (no in-process fallback)."""
    from athanor.workflows.qa.nodes import qa_node

    state = {
        "job_id": "X",
        "qa": {"attempts": [{"sandbox_id": "C", "artifact_prefix": "X/1/"}],
               "qa_yaml_parsed": {}, "acceptance_criteria": [], "sandbox_token": "t"},
    }
    config = {"configurable": {}}

    with pytest.raises(RuntimeError, match="agent_runner"):
        await qa_node(state, config)
