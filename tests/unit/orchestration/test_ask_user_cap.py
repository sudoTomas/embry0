"""Test that the ask-user cap is bumped to 15 when the brainstorming skill is loaded."""

from unittest.mock import AsyncMock, patch

import pytest
from langgraph.types import Command


@pytest.mark.asyncio
async def test_developer_with_brainstorming_skill_uses_cap_15():
    """When developer's agent_skills includes superpowers:brainstorming,
    the ask-user cap is 15 rounds (not 5)."""
    from athanor.workflows.issue_to_pr.nodes import developer_node

    # State: developer has 14 prior rounds + brainstorming skill loaded
    state = {
        "job_id": "JOB",
        "issue_id": "iss-x",
        "repo": "owner/repo",
        "task": "design something",
        "sandbox_container_id": "C",
        "agent_outputs": [],
        "errors": [],
        "agent_question_rounds": 14,
        "pipeline_config": {
            "agent_skills": {"developer": ["superpowers:brainstorming"]},
            "budget_usd": 50.0,
        },
    }

    # Stub run_agent_node to return a result with one new ask_user event
    fake_events = [
        {"type": "agent_ask_user", "question": "Q15?", "category": "design", "options": []}
    ]
    fake_result = {
        "agent_outputs": [{"agent_type": "developer", "is_error": False, "output": "..."}],
        "events": fake_events,
        "total_cost_usd": 0.10,
    }

    async def _run(*args, **kwargs):
        cb = kwargs.get("on_event")
        if cb:
            for ev in fake_events:
                cb(ev)
        return fake_result

    with (
        patch("athanor.workflows.issue_to_pr.nodes.run_agent_node", new=AsyncMock(side_effect=_run)),
        patch("athanor.workflows.issue_to_pr.nodes.get_stream_writer", return_value=lambda _: None),
    ):
        result = await developer_node(state, {"configurable": {"agent_runner": object(), "credentials": {}}})

    # Should NOT be exhausted at round 15 — brainstorming cap is 15
    # (without the bump it would have exhausted at round 5)
    assert isinstance(result, Command)
    assert result.goto != "max_retries"
    update = result.update or {}
    assert not update.get("agent_questions_exhausted")
    assert update.get("agent_question_rounds") == 15


@pytest.mark.asyncio
async def test_developer_without_brainstorming_skill_uses_cap_5():
    """Default cap of 5 still applies when brainstorming isn't loaded."""
    from athanor.workflows.issue_to_pr.nodes import developer_node

    state = {
        "job_id": "JOB",
        "issue_id": "iss-x",
        "repo": "owner/repo",
        "task": "fix something",
        "sandbox_container_id": "C",
        "agent_outputs": [],
        "errors": [],
        "agent_question_rounds": 5,
        "pipeline_config": {
            "agent_skills": {"developer": ["superpowers:writing-plans"]},
            "budget_usd": 50.0,
        },
    }

    fake_events = [
        {"type": "agent_ask_user", "question": "Q6?", "category": "general", "options": []}
    ]
    fake_result = {
        "agent_outputs": [{"agent_type": "developer", "is_error": False, "output": "..."}],
        "events": fake_events,
        "total_cost_usd": 0.05,
    }

    async def _run(*args, **kwargs):
        cb = kwargs.get("on_event")
        if cb:
            for ev in fake_events:
                cb(ev)
        return fake_result

    with (
        patch("athanor.workflows.issue_to_pr.nodes.run_agent_node", new=AsyncMock(side_effect=_run)),
        patch("athanor.workflows.issue_to_pr.nodes.get_stream_writer", return_value=lambda _: None),
    ):
        result = await developer_node(state, {"configurable": {"agent_runner": object(), "credentials": {}}})

    assert isinstance(result, Command)
    update = result.update or {}
    assert update.get("agent_questions_exhausted") is True
