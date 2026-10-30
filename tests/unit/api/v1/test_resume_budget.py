"""``POST /jobs/{id}/resume``: what a person may name to continue a job past its cap is the budget section's (#171).

The literal ``le=1000`` of the request model became ``budget.continue_extra_max_usd``,
read when the request comes in; the answer names who continued the job, for
the ``job.budget_raised`` audit row a raise writes.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from httpx import ASGITransport, AsyncClient

from embry0.api.app import create_app
from embry0.config import Embry0Config
from embry0.settings.sections import CATALOG
from embry0.settings.store import Snapshot, build_snapshot

HEADERS = {"X-Requested-With": "XMLHttpRequest"}


class _Store:
    def __init__(self, **budget: Any) -> None:
        self.budget = budget

    async def snapshot(self) -> Snapshot:
        row = {"scope": "deployment", "data": {"budget": dict(self.budget)}, "generation": 1, "updated_at": None}
        return build_snapshot(CATALOG, [row], token=(), log=False)


@pytest.fixture
def app() -> Any:
    app = create_app(Embry0Config(_env_file=None, auth_dev_mode=True, webhook_dev_mode=True))
    jobs = MagicMock()
    jobs.get = AsyncMock(
        return_value={"job_id": "job-paused123", "status": "paused", "repo": "owner/repo", "issue_id": "iss-1"}
    )
    app.state.jobs_repo = jobs
    app.state.issue_executor = MagicMock(schedule_resume=MagicMock())
    app.state.settings = _Store()
    return app


async def _resume(app: Any, body: dict[str, Any]) -> Any:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        return await client.post("/api/v1/jobs/job-paused123/resume", json=body, headers=HEADERS)


@pytest.mark.asyncio
async def test_an_extra_above_the_sections_most_is_a_422_naming_it(app: Any) -> None:
    resp = await _resume(app, {"choice": "continue_retrying", "extra_budget_usd": 1500})
    assert resp.status_code == 422, "the default most a person may name is 1000, as the literal was"
    (problem,) = resp.json()["detail"]
    assert problem["loc"] == ["body", "extra_budget_usd"]
    assert "budget.continue_extra_max_usd" in problem["msg"]
    app.state.issue_executor.schedule_resume.assert_not_called()


@pytest.mark.asyncio
async def test_the_most_is_read_from_the_section_when_the_request_comes_in(app: Any) -> None:
    app.state.settings.budget = {"continue_extra_max_usd": 50.0}
    assert (await _resume(app, {"choice": "continue_retrying", "extra_budget_usd": 60})).status_code == 422
    app.state.settings.budget = {"continue_extra_max_usd": 2000.0}
    resp = await _resume(app, {"choice": "continue_retrying", "extra_budget_usd": 1500})
    assert resp.status_code == 200, f"1500 is under a raised most of 2000, not a fixed 1000: {resp.text}"


@pytest.mark.asyncio
async def test_the_answer_names_who_continued_the_job(app: Any) -> None:
    resp = await _resume(app, {"choice": "continue_retrying", "extra_budget_usd": 25})
    assert resp.status_code == 200, resp.text
    (_issue, _job, payload), _kw = app.state.issue_executor.schedule_resume.call_args
    assert payload["extra_budget_usd"] == 25
    assert payload.get("actor"), "the resume carries its actor to the node that raises the budget"
