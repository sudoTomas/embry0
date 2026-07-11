"""Auto-answerable agent ask-user: doesn't pause, doesn't count toward cap."""

from unittest.mock import AsyncMock, patch

import pytest


@pytest.mark.asyncio
async def test_auto_answerable_question_does_not_pause_workflow():
    from embry0.workflows.issue_to_pr.nodes import developer_node

    fake_events = [
        {
            "type": "agent_ask_user",
            "question": "Should I add tests too?",
            "category": "general",
            "importance": "auto_answerable",
            "suggested_answer": "yes",
        }
    ]

    async def _run(*args, **kwargs):
        cb = kwargs.get("on_event")
        if cb:
            for ev in fake_events:
                cb(ev)
        return {
            "agent_outputs": [{"agent_type": "developer", "is_error": False, "output": ""}],
            "events": fake_events,
            "total_cost_usd": 0.05,
        }

    state = {
        "job_id": "JOB",
        "issue_id": "iss-x",
        "repo": "owner/repo",
        "task": "x",
        "sandbox_container_id": "C",
        "agent_outputs": [],
        "errors": [],
        "agent_question_rounds": 0,
        "pipeline_config": {"agent_skills": {"developer": []}, "budget_usd": 50.0},
    }

    with (
        patch("embry0.workflows.issue_to_pr.nodes.run_agent_node", new=AsyncMock(side_effect=_run)),
        patch("embry0.workflows.issue_to_pr.nodes.get_stream_writer", return_value=lambda _: None),
    ):
        result = await developer_node(state, {"configurable": {"agent_runner": object(), "credentials": {}}})

    # Should NOT route to ask_user_interrupt; should NOT bump the round counter
    if hasattr(result, "goto"):
        assert result.goto != "ask_user_interrupt"
    update = getattr(result, "update", result if isinstance(result, dict) else {})
    assert update.get("agent_question_rounds", 0) == 0  # unchanged
    assert not update.get("pending_agent_questions")
