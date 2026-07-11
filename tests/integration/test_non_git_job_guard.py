"""End-to-end non-git job guard.

A repo-less (non-git) job creates + persists, and on dispatch lands
``status="failed"`` / ``error_code="ERR_UNSUPPORTED_CONTEXT"`` WITHOUT any
sandbox being created. This drives ``IssueExecutor._run_workflow`` directly
(the same coroutine ``POST /jobs`` schedules) against a real
``JobsRepository`` over the throwaway test DB, with a ``MagicMock()`` sandbox
manager so we can assert its ``create`` is never called — the whole point of
Task 7's init_node guard, which raises ``UnsupportedContextError`` BEFORE it
reads the sandbox manager.
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
    every dependency the guard must never reach. ``issues_repo`` is an
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


async def test_non_git_job_fails_unsupported_without_sandbox(issue_executor, jobs_repo):
    sandbox_mgr = issue_executor._sandbox  # the MagicMock

    # Repo-less job: creates + persists with a non-git context.
    job_id = await jobs_repo.create(task="Research the widget market", context={"type": "none"})
    assert await jobs_repo.get(job_id) is not None  # persisted

    synthetic_issue = {"repo": None, "title": "Research", "body": "", "github_number": None}

    # Dispatch the exact coroutine POST /jobs schedules.
    await issue_executor._run_workflow(None, job_id, synthetic_issue)

    job = await jobs_repo.get(job_id)
    assert job["status"] == "failed"
    assert job["error_code"] == "ERR_UNSUPPORTED_CONTEXT"
    sandbox_mgr.create.assert_not_called()  # no sandbox, no git clone
