"""End-to-end workspace-init guards (RAV-599 guard, reshaped by RAV-600).

All four builtin context types now have init strategies, so the old
"non-git fails ERR_UNSUPPORTED_CONTEXT" behavior narrows to *unknown*
context types — while denied contexts (local outside the allowlist) fail
with their specific code, still with NO sandbox created. A ``none`` context
now proceeds past init. Drives ``IssueExecutor._run_workflow`` directly
(the same coroutine ``POST /jobs`` schedules) against a real
``JobsRepository`` over the throwaway test DB.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from embry0.services.issue_executor import IssueExecutor
from embry0.storage.repositories.jobs import JobsRepository
from embry0.workflows.issue_to_pr.graph import IssueToprWorkflow
from embry0.workflows.registry import WorkflowRegistry

pytestmark = [pytest.mark.requires_postgres, pytest.mark.asyncio]


@pytest.fixture
async def jobs_repo(db_with_migrations) -> JobsRepository:
    return JobsRepository(db_with_migrations)


@pytest.fixture
async def issue_executor(db_with_migrations, jobs_repo) -> IssueExecutor:
    """Construct IssueExecutor as ``embry0/api/app.py`` lifespan does, but
    with a real ``JobsRepository`` over the test DB, a real
    ``WorkflowRegistry`` carrying the issue-to-pr workflow, and MagicMocks for
    every dependency the guards must never reach. ``issues_repo`` is an
    ``AsyncMock`` because ``_run_workflow``'s except path awaits
    ``issues_repo.update`` for the (null) issue.
    """
    import os

    database_url = os.environ["TEST_DATABASE_URL"]

    registry = WorkflowRegistry()
    registry.register(IssueToprWorkflow())

    return IssueExecutor(
        issues_repo=AsyncMock(),
        jobs_repo=jobs_repo,
        traces_repo=MagicMock(),
        workflow_registry=registry,
        database_url=database_url,
        db=None,
        inputs_repo=MagicMock(),
        sandbox_manager=MagicMock(name="sandbox_manager"),
        agent_runner=MagicMock(),
        proxy_manager=MagicMock(),
        qa_app_results_repo=MagicMock(),
        qa_image_tags_repo=MagicMock(),
        qa_volume_state_repo=MagicMock(),
        qa_shared_volume_manager=MagicMock(),
        github_token="test-github-token",
    )


async def test_unknown_context_fails_unsupported_without_sandbox(issue_executor, jobs_repo):
    """A context type with no registered strategy still guards pre-sandbox."""
    sandbox_mgr = issue_executor._sandbox  # the MagicMock

    # Bypasses API-layer JobContext validation deliberately: the guard must
    # hold for rows written outside the API too.
    job_id = await jobs_repo.create(task="Mystery job", context={"type": "bogus"})
    assert await jobs_repo.get(job_id) is not None  # persisted

    synthetic_issue = {"repo": None, "title": "Mystery", "body": "", "github_number": None}
    await issue_executor._run_workflow(None, job_id, synthetic_issue)

    job = await jobs_repo.get(job_id)
    assert job["status"] == "failed"
    assert job["error_code"] == "ERR_UNSUPPORTED_CONTEXT"
    sandbox_mgr.create.assert_not_called()  # no sandbox, no workspace init


async def test_local_context_outside_allowlist_denied_without_sandbox(issue_executor, jobs_repo):
    """Empty LOCAL_CONTEXT_ALLOWLIST (no config injected) fails closed, pre-sandbox."""
    sandbox_mgr = issue_executor._sandbox

    job_id = await jobs_repo.create(task="Analyze local data", context={"type": "local", "path": "/etc"})
    synthetic_issue = {"repo": None, "title": "Analyze", "body": "", "github_number": None}
    await issue_executor._run_workflow(None, job_id, synthetic_issue)

    job = await jobs_repo.get(job_id)
    assert job["status"] == "failed"
    assert job["error_code"] == "ERR_LOCAL_CONTEXT_DENIED"
    sandbox_mgr.create.assert_not_called()


async def test_none_context_proceeds_past_init(issue_executor, jobs_repo):
    """A ``none`` context now initializes: sandbox created, empty /workspace.

    The job still fails LATER (triage runs against a MagicMock agent
    runner) — the assertion is that init is no longer the failure point.
    """
    sandbox_mgr = issue_executor._sandbox
    sandbox_mgr.create = AsyncMock(return_value=("container-none", "a" * 43))
    sandbox_mgr.destroy = AsyncMock()
    docker = MagicMock()
    docker.build_exec_cmd = MagicMock(side_effect=lambda cid, cmd, **kw: ["docker", "exec", cid, *cmd])
    docker.run_cmd = AsyncMock(return_value="")
    sandbox_mgr._docker = docker

    job_id = await jobs_repo.create(task="Research the widget market", context={"type": "none"})
    synthetic_issue = {"repo": None, "title": "Research", "body": "", "github_number": None}
    await issue_executor._run_workflow(None, job_id, synthetic_issue)

    job = await jobs_repo.get(job_id)
    assert job["status"] == "failed"  # downstream (triage) failure is expected here
    assert job["error_code"] != "ERR_UNSUPPORTED_CONTEXT"
    sandbox_mgr.create.assert_awaited_once()
    mkdir_calls = [c for c in docker.run_cmd.await_args_list if "mkdir" in " ".join(map(str, c.args[0]))]
    assert mkdir_calls, "none initializer must create /workspace"
