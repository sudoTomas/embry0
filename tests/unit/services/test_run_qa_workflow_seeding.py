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
