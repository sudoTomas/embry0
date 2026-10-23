"""One job, one job-created audit row, naming who started it.

QA, onboard and pull request review jobs were audited twice, 3 ms apart:
by the executor (``qa.job_created``, actor ``system``) and by the jobs
route (``qa_job_created``, the caller), two spellings of one event. The
executor's row is the one every path writes, so it stays, and it now names
the job's actor.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from httpx import ASGITransport, AsyncClient

from embry0.api.app import create_app
from embry0.config import Embry0Config
from embry0.services.issue_executor import IssueExecutor
from embry0.services.pr_review.dispatcher import DispatchResult


def _executor() -> IssueExecutor:
    ex = IssueExecutor.__new__(IssueExecutor)
    ex._jobs = MagicMock()
    ex._jobs.create = AsyncMock(return_value="job-1")
    ex._db = None
    ex._audit_log_path = None
    ex._track_task = MagicMock()
    ex._run_qa_workflow = MagicMock()
    ex._run_onboard_workflow = MagicMock()
    ex._run_pr_review_workflow = MagicMock()
    return ex


class _Dispatcher:
    """Stands in for the review gates, then starts the job on the executor as the real one does."""

    def __init__(self, executor: IssueExecutor) -> None:
        self._executor = executor

    async def dispatch_manual(self, *, repo: str, number: int, actor: str) -> DispatchResult:
        review = {"number": number, "trigger": "manual"}
        job_id = await self._executor.start_pr_review_job(repo=repo, review=review, dispatched_by=actor)
        return DispatchResult(True, "ok", job_id)


def _app(executor: IssueExecutor) -> Any:
    @asynccontextmanager
    async def _noop(_app):
        yield

    config = Embry0Config(_env_file=None, auth_dev_mode=True, webhook_dev_mode=True)
    app = create_app(config, lifespan_override=_noop)
    app.state.config = config
    app.state.issue_executor = executor
    app.state.pr_review_dispatcher = _Dispatcher(executor)
    jobs = MagicMock()
    jobs.get = AsyncMock(return_value={"job_id": "job-1"})
    app.state.jobs_repo = jobs
    return app


@pytest.mark.parametrize(
    ("body", "action"),
    [
        ({"pipeline": "qa", "repo": "acme/app", "branch": "main"}, "qa.job_created"),
        ({"pipeline": "onboard", "repo": "acme/app"}, "onboard.job_created"),
        ({"pipeline": "pr-review", "repo": "acme/app", "number": 7}, "pr_review.job_created"),
    ],
)
@pytest.mark.asyncio
async def test_one_job_writes_one_job_created_row_naming_its_actor(body: dict[str, Any], action: str) -> None:
    recorder = AsyncMock()
    app = _app(_executor())
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        with (
            patch("embry0.api.v1.jobs.emit_audit", new=recorder),
            patch("embry0.services.issue_executor.emit_audit", new=recorder),
        ):
            resp = await client.post("/api/v1/jobs", json=body, headers={"X-Requested-With": "XMLHttpRequest"})
    assert resp.status_code == 201, resp.text
    rows = [
        (c.args[1], c.kwargs["actor"]) for c in recorder.await_args_list if c.kwargs["details"]["job_id"] == "job-1"
    ]
    assert rows == [(action, "embry0:anonymous")], f"one {body['pipeline']} job wrote these audit rows: {rows}"
