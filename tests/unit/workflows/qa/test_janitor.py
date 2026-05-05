from unittest.mock import AsyncMock, MagicMock

import pytest

from athanor.workflows.qa.janitor import (
    JanitorReport,
    detect_stuck_runs,
    reap_orphan_sandboxes,
)


@pytest.mark.asyncio
async def test_reap_orphan_sandboxes_removes_containers_for_inactive_runs():
    docker = AsyncMock()
    docker.list_containers_with_label = AsyncMock(return_value=[
        {"id": "c1", "labels": {"athanor.qa_job_id": "run-A"}},
        {"id": "c2", "labels": {"athanor.qa_job_id": "run-B"}},
        {"id": "c3", "labels": {"athanor.qa_job_id": "run-A"}},
    ])
    docker.kill = AsyncMock()
    docker.remove = AsyncMock()

    runs_repo = MagicMock()
    runs_repo.is_run_active = AsyncMock(side_effect=lambda rid: rid == "run-B")

    report = await reap_orphan_sandboxes(docker=docker, runs_repo=runs_repo)

    assert sorted(report.reaped_container_ids) == ["c1", "c3"]
    assert docker.kill.await_count == 2
    assert docker.remove.await_count == 2


@pytest.mark.asyncio
async def test_reap_continues_when_one_container_kill_fails():
    docker = AsyncMock()
    docker.list_containers_with_label = AsyncMock(return_value=[
        {"id": "c1", "labels": {"athanor.qa_job_id": "dead"}},
        {"id": "c2", "labels": {"athanor.qa_job_id": "dead"}},
    ])
    docker.kill = AsyncMock(side_effect=[Exception("c1 already dead"), None])
    docker.remove = AsyncMock()
    runs_repo = MagicMock()
    runs_repo.is_run_active = AsyncMock(return_value=False)

    report = await reap_orphan_sandboxes(docker=docker, runs_repo=runs_repo)
    # Both attempted; c1 logged but c2 still reaped.
    assert "c2" in report.reaped_container_ids


@pytest.mark.asyncio
async def test_reap_subtask_sandbox_strips_app_suffix_to_find_parent():
    """Sub-task sandboxes labeled '{parent_job_id}::{app_name}' are reaped when
    the parent run is inactive — the '::hub' suffix is stripped before calling
    is_run_active."""
    docker = AsyncMock()
    docker.list_containers_with_label = AsyncMock(return_value=[
        {"id": "c-sub", "labels": {"athanor.qa_job_id": "run-A::hub"}},
    ])
    docker.kill = AsyncMock()
    docker.remove = AsyncMock()

    runs_repo = MagicMock()
    runs_repo.is_run_active = AsyncMock(return_value=False)

    report = await reap_orphan_sandboxes(docker=docker, runs_repo=runs_repo)

    # Container should be reaped.
    assert "c-sub" in report.reaped_container_ids
    # is_run_active must have been called with the parent id (stripped of ::hub).
    runs_repo.is_run_active.assert_awaited_once_with("run-A")


@pytest.mark.asyncio
async def test_detect_stuck_runs_returns_runs_with_no_progress_for_threshold():
    runs_repo = MagicMock()
    runs_repo.list_in_progress_runs_with_last_progress = AsyncMock(return_value=[
        {"qa_run_id": "run-1", "minutes_since_progress": 5},
        {"qa_run_id": "run-2", "minutes_since_progress": 35},
        {"qa_run_id": "run-3", "minutes_since_progress": 60},
    ])
    stuck = await detect_stuck_runs(runs_repo=runs_repo, threshold_minutes=30)
    assert stuck == ["run-2", "run-3"]
