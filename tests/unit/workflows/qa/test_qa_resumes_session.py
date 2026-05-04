"""QA node loads a prior session, restores it, then re-persists after run."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest


@pytest.mark.asyncio
async def test_qa_loads_prior_session_and_persists_new_one():
    from athanor.agents.session import AgentSession
    from athanor.workflows.qa.nodes import qa_node

    # Pre-existing session for this (job, agent)
    sessions_repo = AsyncMock()
    sessions_repo.get = AsyncMock(
        return_value={
            "job_id": "JOB1",
            "agent_type": "qa",
            "mode": "anthropic_api",
            "messages": [{"role": "user", "content": "earlier"}],
            "session_id": None,
            "session_blob": None,
        }
    )
    sessions_repo.upsert = AsyncMock()

    fake_result = {
        "agent_outputs": [
            {
                "agent_type": "qa",
                "is_error": False,
                "output": "ok",
                "messages": [
                    {"role": "user", "content": "earlier"},
                    {"role": "assistant", "content": "now"},
                ],
                "mode": "anthropic_api",
                "cost_usd": 0.1,
                "duration_ms": 5000,
            }
        ],
    }

    async def _run(*args, **kwargs):
        # Should have received the prior messages as resume_session
        sess: AgentSession = kwargs.get("resume_session")
        assert sess is not None
        assert sess.messages == [{"role": "user", "content": "earlier"}]
        return fake_result

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

    agent_runner = MagicMock()
    db = MagicMock()
    config = {
        "configurable": {
            "agent_runner": agent_runner,
            "db": db,
            "agent_sessions_repo": sessions_repo,
        }
    }

    fake_agent_def_repo = MagicMock()
    fake_agent_def_repo.get = AsyncMock(
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
        patch(
            "athanor.storage.repositories.agent_definitions.AgentDefinitionsRepository",
            return_value=fake_agent_def_repo,
        ),
        patch("athanor.orchestration.nodes.agent.run_agent_node", new=AsyncMock(side_effect=_run)),
    ):
        await qa_node(state, config)

    # Should have persisted the new session with the assistant's reply
    sessions_repo.upsert.assert_awaited_once()
    upsert_kwargs = sessions_repo.upsert.await_args.kwargs
    assert upsert_kwargs["job_id"] == "JOB1"
    assert upsert_kwargs["agent_type"] == "qa"
    assert upsert_kwargs["mode"] == "anthropic_api"
    assert upsert_kwargs["messages"][-1]["content"] == "now"
