"""Developer node loads a prior session, restores it, then re-persists after run."""

from unittest.mock import AsyncMock, patch

import pytest


@pytest.mark.asyncio
async def test_developer_loads_prior_session_and_persists_new_one():
    from athanor.agents.session import AgentSession
    from athanor.workflows.issue_to_pr.nodes import developer_node

    # Pre-existing session for this (job, agent)
    repo = AsyncMock()
    repo.get = AsyncMock(
        return_value={
            "job_id": "J",
            "agent_type": "developer",
            "mode": "anthropic_api",
            "messages": [{"role": "user", "content": "earlier"}],
            "session_id": None,
            "session_blob": None,
        }
    )
    repo.upsert = AsyncMock()

    fake_result = {
        "agent_outputs": [
            {
                "agent_type": "developer",
                "is_error": False,
                "output": "...",
                "messages": [
                    {"role": "user", "content": "earlier"},
                    {"role": "assistant", "content": "now"},
                ],
                "mode": "anthropic_api",
            }
        ],
        "events": [],
        "total_cost_usd": 0.05,
    }

    async def _run(*args, **kwargs):
        # Should have received the prior messages as resume_session
        sess: AgentSession = kwargs.get("resume_session")
        assert sess is not None
        assert sess.messages == [{"role": "user", "content": "earlier"}]
        cb = kwargs.get("on_event")
        if cb:
            for ev in fake_result["events"]:
                cb(ev)
        return fake_result

    state = {
        "job_id": "J",
        "issue_id": "iss",
        "repo": "o/r",
        "task": "x",
        "sandbox_container_id": "C",
        "agent_outputs": [],
        "errors": [],
        "pipeline_config": {"agent_skills": {"developer": []}, "budget_usd": 50.0},
    }

    with (
        patch("athanor.workflows.issue_to_pr.nodes.run_agent_node", new=AsyncMock(side_effect=_run)),
        patch("athanor.workflows.issue_to_pr.nodes.get_stream_writer", return_value=lambda _: None),
    ):
        await developer_node(
            state,
            {
                "configurable": {
                    "agent_runner": object(),
                    "credentials": {},
                    "agent_sessions_repo": repo,
                }
            },
        )

    # Should have persisted the new session with the assistant's reply
    repo.upsert.assert_awaited_once()
    upsert_kwargs = repo.upsert.await_args.kwargs
    assert upsert_kwargs["job_id"] == "J"
    assert upsert_kwargs["agent_type"] == "developer"
    assert upsert_kwargs["mode"] == "anthropic_api"
    assert upsert_kwargs["messages"][-1]["content"] == "now"
