"""Unit tests for qa_node — happy path with mocks."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest


@pytest.mark.asyncio
async def test_qa_node_invokes_run_agent_node():
    """qa_node calls run_agent_node with agent_type='qa'."""
    from embry0.workflows.qa.nodes import qa_node

    state = {
        "job_id": "JOB1",
        "repo": "x/y",
        "qa": {
            "attempts": [
                {
                    "attempt_n": 1,
                    "sandbox_id": "container-id-xyz",
                    "artifact_prefix": "JOB1/1/",
                }
            ],
            "qa_yaml_parsed": {"mode": "dind"},
            "acceptance_criteria": ["home loads"],
            "sandbox_token": "tok",
        },
    }

    agent_runner = MagicMock()  # opaque object, run_agent_node consumes it
    db = MagicMock()
    config = {"configurable": {"agent_runner": agent_runner, "db": db}}

    fake_run = AsyncMock(
        return_value={
            "agent_outputs": [
                {
                    "agent_type": "qa",
                    "is_error": False,
                    "output": "ok",
                    "cost_usd": 0.1,
                    "duration_ms": 5000,
                }
            ],
        }
    )

    fake_repo = MagicMock()
    fake_repo.get = AsyncMock(
        return_value={
            "model": "claude-sonnet-4-6",
            "tools": [],
            "skills": [],
            "system_prompt": "sp",
            "mcp_servers": {},
            "execution_mode": None,
            "auth_mode": None,
        }
    )

    with (
        patch("embry0.storage.repositories.agent_definitions.AgentDefinitionsRepository", return_value=fake_repo),
        patch("embry0.orchestration.nodes.agent.run_agent_node", fake_run),
    ):
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
    from embry0.workflows.qa.nodes import qa_node

    state = {
        "job_id": "JOB",
        "qa": {
            "attempts": [{"attempt_n": 1, "sandbox_id": "C", "artifact_prefix": "JOB/1/"}],
            "qa_yaml_parsed": {},
            "acceptance_criteria": [],
            "sandbox_token": "t",
        },
    }
    agent_runner = MagicMock()
    db = MagicMock()
    config = {"configurable": {"agent_runner": agent_runner, "db": db}}

    fake_run = AsyncMock(
        return_value={
            "agent_outputs": [
                {
                    "agent_type": "qa",
                    "is_error": True,
                    "error_message": "executor crashed: connection refused",
                }
            ],
        }
    )

    fake_repo = MagicMock()
    fake_repo.get = AsyncMock(
        return_value={
            "model": "claude-sonnet-4-6",
            "tools": [],
            "skills": [],
            "system_prompt": "sp",
            "mcp_servers": {},
            "execution_mode": None,
            "auth_mode": None,
        }
    )

    with (
        patch("embry0.storage.repositories.agent_definitions.AgentDefinitionsRepository", return_value=fake_repo),
        patch("embry0.orchestration.nodes.agent.run_agent_node", fake_run),
    ):
        new_state = await qa_node(state, config)

    last = new_state["qa"]["attempts"][-1]
    assert last["last_phase"] is None
    assert "agent_error" in last["exit_reason"]
    assert "connection refused" in last["exit_reason"]


@pytest.mark.asyncio
async def test_qa_node_raises_without_agent_runner():
    """No agent_runner in config — fail loudly (no in-process fallback)."""
    from embry0.workflows.qa.nodes import qa_node

    state = {
        "job_id": "X",
        "qa": {
            "attempts": [{"sandbox_id": "C", "artifact_prefix": "X/1/"}],
            "qa_yaml_parsed": {},
            "acceptance_criteria": [],
            "sandbox_token": "t",
        },
    }
    config = {"configurable": {}}

    with pytest.raises(RuntimeError, match="agent_runner"):
        await qa_node(state, config)


@pytest.mark.parametrize(
    ("criteria_key", "n", "expected_max_turns"),
    [
        ("acceptance_criteria", 0, 100),  # floor
        ("acceptance_criteria", 2, 100),  # 40 + 60 = 100, still floor
        ("acceptance_criteria", 7, 250),  # 40 + 210 — deployed login+breadth run
        ("acceptance_criteria", 20, 400),  # cap
    ],
)
@pytest.mark.asyncio
async def test_qa_node_scales_max_turns_with_criteria(criteria_key, n, expected_max_turns):
    """The agent's turn budget scales with the acceptance-criteria count so a
    criteria-rich deployed-target run (login + breadth) isn't capped at 100."""
    from embry0.workflows.qa.nodes import qa_node

    state = {
        "job_id": "JOB",
        "qa": {
            "attempts": [{"attempt_n": 1, "sandbox_id": "C", "artifact_prefix": "JOB/1/"}],
            "qa_yaml_parsed": {"mode": "process"},
            criteria_key: [f"criterion {i}" for i in range(n)],
            "sandbox_token": "t",
        },
    }
    config = {"configurable": {"agent_runner": MagicMock(), "db": MagicMock()}}

    fake_run = AsyncMock(return_value={"agent_outputs": [{"agent_type": "qa", "is_error": False, "output": "ok"}]})
    fake_repo = MagicMock()
    fake_repo.get = AsyncMock(
        return_value={
            "model": "claude-sonnet-4-6",
            "tools": [],
            "skills": [],
            "system_prompt": "sp",
            "mcp_servers": {},
            "execution_mode": None,
            "auth_mode": None,
        }
    )

    with (
        patch("embry0.storage.repositories.agent_definitions.AgentDefinitionsRepository", return_value=fake_repo),
        patch("embry0.orchestration.nodes.agent.run_agent_node", fake_run),
    ):
        await qa_node(state, config)

    assert fake_run.call_args.kwargs["max_turns"] == expected_max_turns


@pytest.mark.asyncio
async def test_qa_node_scales_max_turns_from_synth_template():
    """Deployed sub-task path carries criteria in qa_yaml_parsed
    (acceptance_criteria_template), not qa['acceptance_criteria']."""
    from embry0.workflows.qa.nodes import qa_node

    state = {
        "job_id": "JOB",
        "qa": {
            "attempts": [{"attempt_n": 1, "sandbox_id": "C", "artifact_prefix": "JOB/1/"}],
            "qa_yaml_parsed": {
                "mode": "process",
                "acceptance_criteria_template": [f"criterion {i}" for i in range(7)],
            },
            "sandbox_token": "t",
        },
    }
    config = {"configurable": {"agent_runner": MagicMock(), "db": MagicMock()}}
    fake_run = AsyncMock(return_value={"agent_outputs": [{"agent_type": "qa", "is_error": False, "output": "ok"}]})
    fake_repo = MagicMock()
    fake_repo.get = AsyncMock(
        return_value={
            "model": "claude-sonnet-4-6",
            "tools": [],
            "skills": [],
            "system_prompt": "sp",
            "mcp_servers": {},
            "execution_mode": None,
            "auth_mode": None,
        }
    )
    with (
        patch("embry0.storage.repositories.agent_definitions.AgentDefinitionsRepository", return_value=fake_repo),
        patch("embry0.orchestration.nodes.agent.run_agent_node", fake_run),
    ):
        await qa_node(state, config)

    assert fake_run.call_args.kwargs["max_turns"] == 250
