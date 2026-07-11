"""Triage node loads a prior session, restores it, then re-persists after run."""

from unittest.mock import AsyncMock, patch

import pytest


@pytest.mark.asyncio
async def test_triage_loads_prior_session_and_persists_new_one():
    from embry0.agents.session import AgentSession
    from embry0.workflows.issue_to_pr.nodes import triage_node

    # Pre-existing session for this (job, agent)
    repo = AsyncMock()
    repo.get = AsyncMock(
        return_value={
            "job_id": "J",
            "agent_type": "triage",
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
                "agent_type": "triage",
                "is_error": False,
                # Triage output must be parseable JSON for the action='proceed' path,
                # otherwise the node short-circuits to a Command(goto=END) before
                # the persist block runs.
                "output": '{"action": "proceed", "reasoning": "ok", "confidence": 0.9}',
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
    }

    with (
        patch("embry0.workflows.issue_to_pr.nodes.run_agent_node", new=AsyncMock(side_effect=_run)),
        patch("embry0.workflows.issue_to_pr.nodes.get_stream_writer", return_value=lambda _: None),
        # apply_repo_preferences_override is async; default to passthrough so
        # the parsed triage_dict reaches result["pipeline_config"] unchanged.
        patch(
            "embry0.orchestration.nodes.triage.apply_repo_preferences_override",
            new=AsyncMock(side_effect=lambda d, *_a, **_kw: d),
        ),
    ):
        await triage_node(
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
    assert upsert_kwargs["agent_type"] == "triage"
    assert upsert_kwargs["mode"] == "anthropic_api"
    assert upsert_kwargs["messages"][-1]["content"] == "now"
