"""A terminal job releases its DinD volumes (#274)."""

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from embry0.services.issue_executor import IssueExecutor


def _executor(*, volume_mgr) -> IssueExecutor:
    ex = IssueExecutor.__new__(IssueExecutor)
    ex._sandbox = MagicMock()
    ex._sandbox.destroy = AsyncMock()
    if volume_mgr is not None:
        ex._qa_shared_volume_manager = volume_mgr
    return ex


@pytest.mark.asyncio
async def test_cleanup_removes_job_volumes_after_the_sandbox():
    order: list[str] = []
    mgr = MagicMock()
    mgr.destroy_for_job = AsyncMock(side_effect=lambda job_id: order.append("volumes"))
    ex = _executor(volume_mgr=mgr)
    ex._sandbox.destroy = AsyncMock(side_effect=lambda cid: order.append("sandbox"))

    await ex._cleanup_sandbox({"sandbox_container_id": "sandbox-job-1"}, "job-1")

    mgr.destroy_for_job.assert_awaited_once_with("job-1")
    assert order == ["sandbox", "volumes"]


@pytest.mark.asyncio
async def test_cleanup_removes_job_volumes_without_a_final_state():
    """A graph that raised has no final state — the volume still has to go."""
    mgr = MagicMock()
    mgr.destroy_for_job = AsyncMock()
    ex = _executor(volume_mgr=mgr)

    await ex._cleanup_sandbox(None, "job-1")

    mgr.destroy_for_job.assert_awaited_once_with("job-1")


@pytest.mark.asyncio
async def test_cleanup_removes_job_volumes_when_sandbox_destroy_fails():
    mgr = MagicMock()
    mgr.destroy_for_job = AsyncMock()
    ex = _executor(volume_mgr=mgr)
    ex._sandbox.destroy = AsyncMock(side_effect=RuntimeError("boom"))

    await ex._cleanup_sandbox({"sandbox_container_id": "sandbox-job-1"}, "job-1")

    mgr.destroy_for_job.assert_awaited_once_with("job-1")


@pytest.mark.asyncio
async def test_cleanup_swallows_volume_manager_errors():
    mgr = MagicMock()
    mgr.destroy_for_job = AsyncMock(side_effect=RuntimeError("daemon down"))
    ex = _executor(volume_mgr=mgr)

    await ex._cleanup_sandbox(None, "job-1")  # must not raise


@pytest.mark.asyncio
async def test_cleanup_without_a_volume_manager_is_a_noop():
    ex = _executor(volume_mgr=None)
    await ex._cleanup_sandbox(None, "job-1")


@pytest.mark.asyncio
async def test_cancel_job_removes_job_volumes(monkeypatch):
    import embry0.orchestration.checkpoint as ckpt_mod
    import embry0.services.issue_executor as exec_mod

    monkeypatch.setattr(ckpt_mod, "purge_thread", AsyncMock())
    monkeypatch.setattr(exec_mod, "emit_audit", AsyncMock())

    order: list[str] = []
    mgr = MagicMock()
    mgr.destroy_for_job = AsyncMock(side_effect=lambda job_id: order.append("volumes"))
    ex = _executor(volume_mgr=mgr)
    ex._sandbox.destroy = AsyncMock(side_effect=lambda cid: order.append("sandbox"))
    ex._background_tasks = set()
    ex._tasks_by_job = {}
    ex._event_bus = None
    ex._database_url = "postgres://stub"
    ex._audit_log_path = None
    ex._db = MagicMock()
    ex._jobs = MagicMock()
    ex._jobs.update = AsyncMock()
    ex._jobs.get = AsyncMock(return_value=None)
    ex._issues = MagicMock()
    ex._issues.update = AsyncMock()

    started = asyncio.Event()

    async def _slow() -> None:
        started.set()
        await asyncio.sleep(10)

    ex._track_task(_slow(), kind="qa", job_id="job-1")
    await started.wait()

    await ex.cancel_job("job-1", actor="tester")

    mgr.destroy_for_job.assert_awaited_once_with("job-1")
    assert order == ["sandbox", "volumes"]


@pytest.mark.asyncio
async def test_qa_unexpected_interrupt_still_releases_sandbox_and_volumes():
    """The QA graph does not interrupt today; if it ever does the job is failed
    on the spot — a terminal path, so it must clean up like the others."""
    from unittest.mock import patch

    ex = IssueExecutor(
        issues_repo=MagicMock(),
        jobs_repo=AsyncMock(),
        traces_repo=MagicMock(),
        workflow_registry=MagicMock(),
        database_url="postgresql://test/test",
    )
    ex._jobs.update = AsyncMock()
    ex._announce_finished = AsyncMock()
    ex._sandbox = MagicMock()
    ex._sandbox.destroy = AsyncMock()
    mgr = MagicMock()
    mgr.destroy_for_job = AsyncMock()
    ex._qa_shared_volume_manager = mgr

    async def interrupted(initial_state, *args, **kwargs):
        return {"sandbox_container_id": "sandbox-job-1"}, True, None

    with patch.object(ex, "_execute_workflow_stream", side_effect=interrupted):
        await ex._run_qa_workflow(job_id="job-1", repo="org/r", branch="feature/x", qa_overrides={})

    assert ex._jobs.update.await_args.kwargs["status"] == "failed"
    ex._sandbox.destroy.assert_awaited_once_with("sandbox-job-1")
    mgr.destroy_for_job.assert_awaited_once_with("job-1")


@pytest.mark.asyncio
async def test_cancel_job_mid_qa_fanout_releases_subtask_sandboxes_and_qa_networks(monkeypatch):
    """#328: a job cancelled mid fan-out must not leave its QA resources to the reaper."""
    import embry0.orchestration.checkpoint as ckpt_mod
    import embry0.services.issue_executor as exec_mod
    from embry0.execution.runtime.protocol import ContainerSummary

    monkeypatch.setattr(ckpt_mod, "purge_thread", AsyncMock())
    monkeypatch.setattr(exec_mod, "emit_audit", AsyncMock())

    order: list[str] = []
    mgr = MagicMock()
    mgr.destroy_for_job = AsyncMock(side_effect=lambda job_id: order.append("volumes"))
    ex = _executor(volume_mgr=mgr)
    ex._sandbox.destroy = AsyncMock(side_effect=lambda cid: order.append(f"destroy {cid}"))

    def _summary(name: str) -> ContainerSummary:
        return ContainerSummary(id=f"id-{name}", name=name, created_at="", status="Up", running_for="")

    runtime = MagicMock()
    # Docker's name filter is a substring match: job-10's sandbox comes back too.
    runtime.list_containers = AsyncMock(
        side_effect=lambda name=None, label=None, **kw: (
            [_summary("sandbox-job-1__web"), _summary("sandbox-job-1__api"), _summary("sandbox-job-10__web")]
            if name == "sandbox-job-1__"
            else []
        )
    )
    runtime.list_volumes = AsyncMock(return_value=[])
    runtime.remove_network = AsyncMock(side_effect=lambda net, timeout=10: order.append(f"rm net {net}"))
    ex._sandbox.runtime = runtime
    ex._background_tasks = set()
    ex._tasks_by_job = {}
    ex._event_bus = None
    ex._database_url = "postgres://stub"
    ex._audit_log_path = None
    ex._db = MagicMock()
    ex._jobs = MagicMock()
    ex._jobs.update = AsyncMock()
    ex._jobs.get = AsyncMock(return_value=None)
    ex._issues = MagicMock()
    ex._issues.update = AsyncMock()

    await ex.cancel_job("job-1", actor="tester")

    assert order == [
        "destroy sandbox-job-1",
        "destroy sandbox-job-1__web",
        "destroy sandbox-job-1__api",
        "rm net qa-net-job-1",
        "rm net qa-net-job-1__web",
        "rm net qa-net-job-1__api",
        "volumes",
    ], f"cancel must destroy the job's sub-task sandboxes, then remove its QA networks, then its volumes: {order}"
    labels = {c.kwargs.get("label") for c in runtime.list_containers.await_args_list}
    assert "embry0.qa_job_id=job-1__web" in labels, f"agent-started QA containers were never looked up: {labels}"
