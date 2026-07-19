"""EMB-39: _run_workflow seeds issue labels into initial_state.

Labels feed the conditional-criteria `labels:` predicates evaluated by the
QA orchestrator. Standalone QA jobs have no issue, so only the issue→PR
path seeds them.
"""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from embry0.services.issue_executor import IssueExecutor


def _make_executor(captured: dict) -> IssueExecutor:
    jobs = MagicMock()
    jobs.get = AsyncMock(return_value={"trace_id": None, "pipeline_config": None})
    jobs.update = AsyncMock()

    issues = MagicMock()
    issues.update = AsyncMock()

    async def fake_execute_workflow_stream(input_value, issue_id, job_id):
        captured["initial_state"] = input_value
        return None, False, None

    executor = IssueExecutor.__new__(IssueExecutor)
    executor._jobs = jobs
    executor._issues = issues
    executor._traces = MagicMock()
    executor._registry = MagicMock()
    executor._database_url = ""
    executor._audit_log_path = None
    executor._db = None
    executor._inputs = None
    executor._config = SimpleNamespace(environment_secret_key="k")
    executor._sandbox = None
    executor._agent_runner = None
    executor._proxy = None
    executor._event_bus = None
    executor._env_repo = None
    executor._background_tasks = set()
    executor._tasks_by_job = {}
    executor._execute_workflow_stream = fake_execute_workflow_stream  # type: ignore[assignment]
    executor._cleanup_sandbox = AsyncMock()  # type: ignore[assignment]
    return executor


@pytest.mark.asyncio
async def test_run_workflow_seeds_issue_labels():
    captured: dict = {}
    executor = _make_executor(captured)
    issue = {
        "title": "T",
        "body": "",
        "repo": "foo/bar",
        "github_number": 7,
        "labels": ["embry0", "qa:deep"],
    }
    await executor._run_workflow("iss-1", "job-1", issue)
    assert captured["initial_state"]["issue_labels"] == ["embry0", "qa:deep"]


@pytest.mark.asyncio
async def test_run_workflow_labels_default_empty():
    captured: dict = {}
    executor = _make_executor(captured)
    issue = {"title": "T", "body": "", "repo": "foo/bar", "github_number": 7}
    await executor._run_workflow("iss-1", "job-1", issue)
    assert captured["initial_state"]["issue_labels"] == []
