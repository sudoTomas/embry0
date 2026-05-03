"""Plan C Task 8 — end-to-end conversation continuity smoke.

Across two consecutive ``developer_node`` invocations on the same job:

  1. First invocation: ``resume_session`` is None (no prior session row).
     The simulated agent emits a 2-message conversation; the node must
     persist it via ``AgentSessionsRepository.upsert``.
  2. Second invocation: ``resume_session`` is an ``AgentSession`` carrying
     the prior turn's messages (NOT None). The simulated agent appends
     a third message; the node persists the appended list.

Verifies the orchestration plumbing only — ``run_agent_node`` and the
sandbox/agent_runner are mocked. No live Claude, no live sandbox.

Uses the ``app`` fixture purely to get a fully-migrated Postgres pool
hanging off ``app._transport.app.state.db`` (matches the pattern from
``test_phase_inbound_smoke.py``). Migration 23 creates the
``agent_sessions`` table; the integration conftest applies all migrations
during ``setup_database``, so the table exists when the test body runs.

Cleanup deletes the seeded job + session rows in a finally block so reruns
do not leak rows.
"""

from __future__ import annotations

import uuid
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
from httpx import AsyncClient


@pytest.mark.requires_postgres
@pytest.mark.asyncio
async def test_developer_session_persists_and_resumes_with_user_answer(
    app: AsyncClient,
) -> None:
    """End-to-end: developer_node persists session on call 1, restores on call 2."""
    from athanor.agents.session import AgentSession
    from athanor.storage.repositories.agent_sessions import AgentSessionsRepository
    from athanor.workflows.issue_to_pr.nodes import developer_node

    # ---- 1. Live DB pool + repo (the conftest already applied migrations 1-23) ----
    fastapi_app = app._transport.app  # noqa: SLF001
    db = fastapi_app.state.db
    repo = AgentSessionsRepository(db)

    suffix = uuid.uuid4().hex[:8]
    job_id = f"job-continuity-smoke-{suffix}"
    issue_id = f"iss-continuity-smoke-{suffix}"
    repo_full = "owner/continuity-smoke"

    # State shape mirrors the unit test in tests/unit/workflows/test_developer_resumes_session.py.
    # developer_node only reads scalar fields off ``state``; the agent_runner is opaque
    # because run_agent_node is mocked.
    state: dict[str, Any] = {
        "job_id": job_id,
        "issue_id": issue_id,
        "repo": repo_full,
        "task": "smoke task — verify continuity",
        "sandbox_container_id": "stub-container",
        "agent_outputs": [],
        "errors": [],
        "pipeline_config": {"agent_skills": {"developer": []}, "budget_usd": 50.0},
    }

    # The two synthetic conversations the mocked agent will return on each call.
    first_messages = [
        {"role": "user", "content": "hi"},
        {"role": "assistant", "content": "hi back"},
    ]
    second_messages = [
        *first_messages,
        {"role": "user", "content": "follow-up after pause"},
        {"role": "assistant", "content": "got it"},
    ]

    # captured_resume_sessions[i] == the resume_session value run_agent_node received on call i.
    captured_resume_sessions: list[AgentSession | None] = []

    def _make_mock_run(messages_to_return: list[dict[str, Any]]):
        async def _mock_run(*args: Any, **kwargs: Any) -> dict[str, Any]:
            captured_resume_sessions.append(kwargs.get("resume_session"))
            return {
                "agent_outputs": [
                    {
                        "agent_type": "developer",
                        "is_error": False,
                        "output": "...",
                        "messages": messages_to_return,
                        "mode": "anthropic_api",
                    }
                ],
                "events": [],
                "total_cost_usd": 0.01,
            }

        return _mock_run

    config = {
        "configurable": {
            "agent_runner": object(),
            "credentials": {},
            "agent_sessions_repo": repo,
        }
    }

    try:
        # ---- 2. Seed the parent jobs row (FK requirement) ----
        await db.execute(
            """
            INSERT INTO jobs (job_id, repo, task, status)
            VALUES ($1, $2, $3, 'pending')
            """,
            job_id,
            repo_full,
            "smoke task",
        )

        # ---- 3. Sanity: no prior session row exists ----
        assert await repo.get(job_id, "developer") is None

        # ---- 4. First developer_node invocation ----
        with (
            patch(
                "athanor.workflows.issue_to_pr.nodes.run_agent_node",
                new=AsyncMock(side_effect=_make_mock_run(first_messages)),
            ),
            patch(
                "athanor.workflows.issue_to_pr.nodes.get_stream_writer",
                return_value=lambda _ev: None,
            ),
        ):
            await developer_node(state, config)

        # ---- 5. Verify call 1 saw resume_session=None ----
        assert len(captured_resume_sessions) == 1
        assert captured_resume_sessions[0] is None, (
            "first developer_node call must not have a prior session"
        )

        # ---- 6. Verify the first turn was persisted ----
        row = await repo.get(job_id, "developer")
        assert row is not None, "developer_node must have persisted the agent session"
        assert row["mode"] == "anthropic_api"
        assert row["messages"] == first_messages
        assert row["session_id"] is None
        assert row["session_blob"] is None

        # ---- 7. Second developer_node invocation (simulates resume after pause) ----
        # Reset captured state's agent_outputs so we can observe the new turn cleanly.
        state["agent_outputs"] = []

        with (
            patch(
                "athanor.workflows.issue_to_pr.nodes.run_agent_node",
                new=AsyncMock(side_effect=_make_mock_run(second_messages)),
            ),
            patch(
                "athanor.workflows.issue_to_pr.nodes.get_stream_writer",
                return_value=lambda _ev: None,
            ),
        ):
            await developer_node(state, config)

        # ---- 8. Verify call 2 received the prior conversation as resume_session ----
        assert len(captured_resume_sessions) == 2
        resumed = captured_resume_sessions[1]
        assert resumed is not None, (
            "second developer_node call MUST receive a non-None resume_session — "
            "this is the core continuity assertion"
        )
        assert isinstance(resumed, AgentSession)
        assert resumed.mode == "anthropic_api"
        assert resumed.job_id == job_id
        assert resumed.agent_type == "developer"
        assert resumed.messages == first_messages, (
            "resume_session must carry the EXACT prior turn — not a stitched prompt"
        )

        # ---- 9. Verify the appended conversation was persisted ----
        row2 = await repo.get(job_id, "developer")
        assert row2 is not None
        assert row2["messages"] == second_messages
        assert row2["mode"] == "anthropic_api"
    finally:
        # Best-effort cleanup. delete_for_job() is idempotent; jobs row delete
        # cascades to agent_sessions via the FK ON DELETE CASCADE, but call
        # delete_for_job explicitly so a partial seed (no jobs row) still
        # purges any session rows that snuck in.
        try:
            await repo.delete_for_job(job_id)
        except Exception:
            pass
        try:
            await db.execute("DELETE FROM jobs WHERE job_id = $1", job_id)
        except Exception:
            pass


@pytest.mark.requires_postgres
@pytest.mark.asyncio
async def test_developer_session_persists_and_resumes_claude_max_mode(
    app: AsyncClient,
) -> None:
    """End-to-end claude_max mode: developer_node persists session_id + blob,
    restores them as resume_session on the next turn — proving the canonical
    projects/<cwd>/<id>.jsonl round-trip works after Plan 2."""
    from athanor.agents.session import AgentSession
    from athanor.storage.repositories.agent_sessions import AgentSessionsRepository
    from athanor.workflows.issue_to_pr.nodes import developer_node

    fastapi_app = app._transport.app  # noqa: SLF001
    db = fastapi_app.state.db
    repo = AgentSessionsRepository(db)

    suffix = uuid.uuid4().hex[:8]
    job_id = f"job-continuity-oauth-{suffix}"
    issue_id = f"iss-continuity-oauth-{suffix}"
    repo_full = "owner/continuity-oauth"

    state: dict[str, Any] = {
        "job_id": job_id,
        "issue_id": issue_id,
        "repo": repo_full,
        "task": "smoke task — verify oauth continuity",
        "sandbox_container_id": "stub-container",
        "agent_outputs": [],
        "errors": [],
        "pipeline_config": {"agent_skills": {"developer": []}, "budget_usd": 50.0},
    }

    first_session_id = "sess-oauth-1"
    first_blob = b'{"role":"user","content":"hi"}\n{"role":"assistant","content":"hi back"}\n'
    second_session_id = "sess-oauth-1"  # same id — CLI keeps it across resumes
    second_blob = first_blob + b'{"role":"user","content":"follow-up"}\n{"role":"assistant","content":"got it"}\n'

    captured_resume_sessions: list[AgentSession | None] = []

    def _make_mock_run(session_id: str, blob: bytes):
        async def _mock_run(*args: Any, **kwargs: Any) -> dict[str, Any]:
            captured_resume_sessions.append(kwargs.get("resume_session"))
            return {
                "agent_outputs": [
                    {
                        "agent_type": "developer",
                        "is_error": False,
                        "output": "...",
                        "session_id": session_id,
                        "session_blob": blob,
                        "mode": "claude_max",
                    }
                ],
                "events": [],
                "total_cost_usd": 0.01,
            }

        return _mock_run

    config = {
        "configurable": {
            "agent_runner": object(),
            # The presence of oauth_token signals claude_max mode to developer_node.
            "credentials": {"oauth_token": "fake-oauth-token-for-test"},
            "agent_sessions_repo": repo,
        }
    }

    try:
        await db.execute(
            """
            INSERT INTO jobs (job_id, repo, task, status)
            VALUES ($1, $2, $3, 'pending')
            """,
            job_id,
            repo_full,
            "smoke task",
        )

        # Sanity: no prior session row.
        assert await repo.get(job_id, "developer") is None

        # ---- First developer_node invocation ----
        with (
            patch(
                "athanor.workflows.issue_to_pr.nodes.run_agent_node",
                new=AsyncMock(side_effect=_make_mock_run(first_session_id, first_blob)),
            ),
            patch(
                "athanor.workflows.issue_to_pr.nodes.get_stream_writer",
                return_value=lambda _ev: None,
            ),
        ):
            await developer_node(state, config)

        assert len(captured_resume_sessions) == 1
        assert captured_resume_sessions[0] is None, (
            "first developer_node call must not have a prior session"
        )

        row = await repo.get(job_id, "developer")
        assert row is not None, "developer_node must have persisted the agent session"
        assert row["mode"] == "claude_max"
        assert row["session_id"] == first_session_id
        assert row["session_blob"] == first_blob
        assert row["messages"] is None

        # ---- Second developer_node invocation ----
        state["agent_outputs"] = []

        with (
            patch(
                "athanor.workflows.issue_to_pr.nodes.run_agent_node",
                new=AsyncMock(side_effect=_make_mock_run(second_session_id, second_blob)),
            ),
            patch(
                "athanor.workflows.issue_to_pr.nodes.get_stream_writer",
                return_value=lambda _ev: None,
            ),
        ):
            await developer_node(state, config)

        assert len(captured_resume_sessions) == 2
        resumed = captured_resume_sessions[1]
        assert resumed is not None, (
            "second developer_node call MUST receive a non-None resume_session — "
            "core continuity assertion for claude_max mode"
        )
        assert isinstance(resumed, AgentSession)
        assert resumed.mode == "claude_max"
        assert resumed.job_id == job_id
        assert resumed.agent_type == "developer"
        assert resumed.session_id == first_session_id
        assert resumed.session_blob == first_blob, (
            "resume_session must carry the EXACT prior session blob bytes — "
            "not a stitched conversation"
        )

        row2 = await repo.get(job_id, "developer")
        assert row2 is not None
        assert row2["session_id"] == second_session_id
        assert row2["session_blob"] == second_blob
        assert row2["mode"] == "claude_max"
    finally:
        try:
            await repo.delete_for_job(job_id)
        except Exception:
            pass
        try:
            await db.execute("DELETE FROM jobs WHERE job_id = $1", job_id)
        except Exception:
            pass
