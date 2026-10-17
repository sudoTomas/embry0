"""#29: an onboard job started without a branch analyzes the repo's own base branch.

The dashboard's agent path leaves the branch empty to mean "this repo's
default". The API used to fill in the literal "main", so a repo whose
integration branch is anything else was analyzed on a branch that may not
exist. The executor now asks the same resolver every other job uses
(preference, then the forge's default branch, then "main").
"""

from __future__ import annotations

import os
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from embry0.services.issue_executor import IssueExecutor


def _executor() -> IssueExecutor:
    ex = IssueExecutor.__new__(IssueExecutor)
    ex._jobs = MagicMock(create=AsyncMock(return_value="job-1"))
    ex._db = None
    ex._audit_log_path = None
    ex._track_task = MagicMock()
    return ex


async def _start(ex: IssueExecutor, **kwargs: Any) -> dict[str, Any]:
    with (
        patch("embry0.services.issue_executor.emit_audit", new=AsyncMock()),
        patch.object(ex, "_run_onboard_workflow", new=MagicMock()),
    ):
        await ex.start_onboard_job(repo="group/proj", dispatched_by="api", **kwargs)
    return ex._jobs.create.await_args.kwargs


async def test_no_branch_resolves_the_repos_base_branch() -> None:
    ex = _executor()
    with patch(
        "embry0.services._executor_outcomes._repo_base_branch", new=AsyncMock(return_value="develop")
    ) as resolve:
        created = await _start(ex, branch=None)
    resolve.assert_awaited_once_with(ex, "group/proj")
    assert created["pipeline_config"]["branch"] == "develop", (
        f"an onboard job without a branch did not use the resolved base branch: {created['pipeline_config']}"
    )
    assert "group/proj@develop" in created["task"], created["task"]
    launch = ex._track_task.call_args.kwargs["launch"]
    assert launch["branch"] == "develop", f"a restart would relaunch on the wrong branch: {launch}"


async def test_explicit_branch_skips_the_resolver() -> None:
    ex = _executor()
    with patch("embry0.services._executor_outcomes._repo_base_branch", new=AsyncMock()) as resolve:
        created = await _start(ex, branch="release")
    resolve.assert_not_awaited()
    assert created["pipeline_config"]["branch"] == "release"


@pytest.fixture
def client() -> TestClient:
    from embry0.api.app import create_app
    from embry0.api.deps import bust_api_key_cache
    from embry0.config import Embry0Config

    bust_api_key_cache()
    with patch.dict(os.environ, {}, clear=True):
        config = Embry0Config(_env_file=None, auth_dev_mode=False, webhook_dev_mode=False, api_key="master-key")
    app = create_app(config)
    app.state.db = None
    app.state.provider_repo = None
    app.state.jobs_repo = MagicMock(get=AsyncMock(return_value={"job_id": "job-new"}))
    app.state.issue_executor = MagicMock(start_onboard_job=AsyncMock(return_value="job-new"))
    return TestClient(app, raise_server_exceptions=False)


def _post(client: TestClient, body: dict[str, Any]) -> Any:
    return client.post(
        "/api/v1/jobs",
        json=body,
        headers={"Authorization": "Bearer master-key", "X-Requested-With": "XMLHttpRequest"},
    )


def test_api_passes_an_absent_branch_through(client: TestClient) -> None:
    r = _post(client, {"repo": "group/proj", "pipeline": "onboard"})
    assert r.status_code == 201, r.text
    start = client.app.state.issue_executor.start_onboard_job  # type: ignore[attr-defined]
    assert start.await_args.kwargs["branch"] is None, (
        f"the API chose a branch instead of letting the executor resolve it: {start.await_args.kwargs}"
    )


def test_api_passes_an_explicit_branch_through(client: TestClient) -> None:
    r = _post(client, {"repo": "group/proj", "pipeline": "onboard", "branch": "dev", "skip_smoke": True})
    assert r.status_code == 201, r.text
    kwargs = client.app.state.issue_executor.start_onboard_job.await_args.kwargs  # type: ignore[attr-defined]
    assert kwargs["branch"] == "dev"
    assert kwargs["skip_smoke"] is True
