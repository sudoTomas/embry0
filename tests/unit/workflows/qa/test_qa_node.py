"""Unit tests for qa_node — happy path with mocks."""

from unittest.mock import AsyncMock, MagicMock

import pytest


@pytest.mark.asyncio
async def test_qa_node_invokes_executor():
    """qa_node builds an invocation for agent_type=qa and runs it."""
    from athanor.workflows.qa.nodes import qa_node

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
            "qa_yaml_parsed": {
                "mode": "dind",
                "frontend_url": "http://x",
                "startup": {"command": "x", "ready_checks": [{"http": "http://x"}], "boot_timeout_seconds": 60},
                "acceptance_criteria_template": [],
                "qa_required": "auto",
            },
            "acceptance_criteria": ["home loads"],
            "sandbox_token": "tok",
        },
    }

    fake_invocation = MagicMock()
    fake_executor = AsyncMock()
    fake_executor.execute = AsyncMock(return_value={"phase_reached": "report", "exit_reason": None})

    agent_resolver = AsyncMock()
    agent_resolver.build_invocation = AsyncMock(return_value=fake_invocation)
    executor_factory = MagicMock()
    executor_factory.select_executor = MagicMock(return_value=fake_executor)

    config = {
        "configurable": {
            "agent_resolver": agent_resolver,
            "executor_factory": executor_factory,
        }
    }

    new_state = await qa_node(state, config)

    agent_resolver.build_invocation.assert_awaited_once()
    fake_executor.execute.assert_awaited_once()
    last_attempt = new_state["qa"]["attempts"][-1]
    assert last_attempt["last_phase"] == "report"


@pytest.mark.asyncio
async def test_qa_node_records_failure_phase():
    """If the executor returns boot_timeout, record it in the attempt."""
    from athanor.workflows.qa.nodes import qa_node

    state = {
        "job_id": "JOB",
        "qa": {
            "attempts": [{"attempt_n": 1, "sandbox_id": "C", "artifact_prefix": "x/1/"}],
            "qa_yaml_parsed": {},
            "acceptance_criteria": [],
            "sandbox_token": "t",
        },
    }
    agent_resolver = AsyncMock(build_invocation=AsyncMock(return_value=MagicMock()))
    fake_executor = AsyncMock(execute=AsyncMock(return_value={"phase_reached": "boot", "exit_reason": "boot_timeout"}))
    executor_factory = MagicMock(select_executor=MagicMock(return_value=fake_executor))

    config = {"configurable": {"agent_resolver": agent_resolver, "executor_factory": executor_factory}}
    new_state = await qa_node(state, config)

    last = new_state["qa"]["attempts"][-1]
    assert last["last_phase"] == "boot"
    assert last["exit_reason"] == "boot_timeout"
