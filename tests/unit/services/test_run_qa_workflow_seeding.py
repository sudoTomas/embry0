"""Phase-C2: _run_qa_workflow plumbs base_branch + force_all_apps from
qa_overrides into the initial JobState passed into the workflow."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest


def _make_executor():
    from athanor.services.issue_executor import IssueExecutor
    e = IssueExecutor(
        issues_repo=MagicMock(),
        jobs_repo=AsyncMock(),
        traces_repo=MagicMock(),
        workflow_registry=MagicMock(),
        database_url="postgresql://test/test",
    )
    e._jobs.update = AsyncMock()
    return e


@pytest.mark.asyncio
async def test_base_branch_seeded_into_state():
    """When qa_overrides.base_branch is set, it lands at
    state['base_branch'] in the initial workflow state."""
    captured: dict = {}

    async def fake_execute(initial_state, *args, **kwargs):
        captured.update(initial_state)
        return None, False, None

    executor = _make_executor()

    with patch.object(executor, "_execute_workflow_stream", side_effect=fake_execute):
        await executor._run_qa_workflow(
            job_id="job-1",
            repo="org/r",
            branch="feature/x",
            qa_overrides={"base_branch": "master"},
        )

    assert captured.get("base_branch") == "master"


@pytest.mark.asyncio
async def test_force_all_apps_seeded_into_qa_state():
    """When qa_overrides.force_all_apps is True, it lands at
    state['qa']['force_all_apps']."""
    captured: dict = {}

    async def fake_execute(initial_state, *args, **kwargs):
        captured.update(initial_state)
        return None, False, None

    executor = _make_executor()

    with patch.object(executor, "_execute_workflow_stream", side_effect=fake_execute):
        await executor._run_qa_workflow(
            job_id="job-2",
            repo="org/r",
            branch="feature/x",
            qa_overrides={"force_all_apps": True},
        )

    assert captured.get("qa", {}).get("force_all_apps") is True


@pytest.mark.asyncio
async def test_base_branch_absent_when_not_set():
    """When qa_overrides has no base_branch, state['base_branch'] is None
    (or absent — both acceptable)."""
    captured: dict = {}

    async def fake_execute(initial_state, *args, **kwargs):
        captured.update(initial_state)
        return None, False, None

    executor = _make_executor()

    with patch.object(executor, "_execute_workflow_stream", side_effect=fake_execute):
        await executor._run_qa_workflow(
            job_id="job-3",
            repo="org/r",
            branch="feature/x",
            qa_overrides={},
        )

    # Either absent or None — both acceptable
    assert captured.get("base_branch") is None


@pytest.mark.asyncio
async def test_qa_workflow_injects_merged_env_vars_into_state():
    """Regression for 2026-05-17: the standalone QA path must inject
    per-repo env vars into state['user_env_vars']. It historically only
    happened on the issue→PR path, so credentials seeded for a repo never
    reached the QA sandboxes (directory: 'Missing AZURE_*', credit kept
    hitting Auth0 despite RC_QA_BYPASS being in the store)."""
    captured: dict = {}

    async def fake_execute(initial_state, *args, **kwargs):
        captured.update(initial_state)
        return None, False, None

    executor = _make_executor()

    seeded = [
        {"key": "RC_QA_BYPASS", "value": "1", "scope": "app"},
        {"key": "AZURE_TENANT_ID", "value": "tid", "scope": "app"},
    ]

    with (
        patch.object(executor, "_execute_workflow_stream", side_effect=fake_execute),
        patch.object(
            executor, "_load_merged_env_vars", AsyncMock(return_value=seeded)
        ) as mock_load,
    ):
        await executor._run_qa_workflow(
            job_id="job-env",
            repo="client-project/command-center",
            branch="feat/athanor-qa-config",
            qa_overrides={"force_all_apps": True},
        )

    mock_load.assert_awaited_once_with("client-project/command-center", "job-env")
    assert captured.get("user_env_vars") == seeded


@pytest.mark.asyncio
async def test_qa_workflow_total_cost_from_traces():
    """Regression 2026-05-18: the QA fan-out subgraph never propagates
    per-agent total_cost_usd up to the parent workflow state (only the
    issue→PR path accumulates it via run_agent_node), so the state value
    is always 0.0 for QA and every QA job row showed $0.00. Trace rows
    are written per agent under the parent job_id regardless of path and
    are the same source GET /jobs cost_breakdown derives from — the job
    row must take its total from there so the two stay consistent."""
    executor = _make_executor()
    executor._traces.total_cost_for_job = AsyncMock(return_value=8.16)

    async def fake_execute(initial_state, *args, **kwargs):
        # QA finished; state carries 0.0 — the exact bug condition.
        return ({"qa": {"final_status": "failed"}, "total_cost_usd": 0.0}, False, None)

    with patch.object(executor, "_execute_workflow_stream", side_effect=fake_execute):
        await executor._run_qa_workflow(
            job_id="job-cost", repo="org/r", branch="b", qa_overrides={},
        )

    executor._traces.total_cost_for_job.assert_awaited_once_with("job-cost")
    _, kwargs = executor._jobs.update.call_args
    assert kwargs["total_cost_usd"] == 8.16


@pytest.mark.asyncio
async def test_qa_workflow_cost_falls_back_to_state_when_no_traces():
    """If there are no trace rows (traces total 0.0) fall back to the
    state value so a future path that does accumulate isn't zeroed out."""
    executor = _make_executor()
    executor._traces.total_cost_for_job = AsyncMock(return_value=0.0)

    async def fake_execute(initial_state, *args, **kwargs):
        return ({"qa": {"final_status": "passed"}, "total_cost_usd": 2.5}, False, None)

    with patch.object(executor, "_execute_workflow_stream", side_effect=fake_execute):
        await executor._run_qa_workflow(
            job_id="job-cost2", repo="org/r", branch="b", qa_overrides={},
        )

    _, kwargs = executor._jobs.update.call_args
    assert kwargs["total_cost_usd"] == 2.5


@pytest.mark.asyncio
async def test_qa_workflow_omits_user_env_vars_when_none_seeded():
    """No seeded env → no user_env_vars key (don't inject an empty list)."""
    captured: dict = {}

    async def fake_execute(initial_state, *args, **kwargs):
        captured.update(initial_state)
        return None, False, None

    executor = _make_executor()

    with (
        patch.object(executor, "_execute_workflow_stream", side_effect=fake_execute),
        patch.object(
            executor, "_load_merged_env_vars", AsyncMock(return_value=None)
        ),
    ):
        await executor._run_qa_workflow(
            job_id="job-noenv",
            repo="org/r",
            branch="b",
            qa_overrides={},
        )

    assert "user_env_vars" not in captured
