"""The watcher and spent account waits (spec 2026-10-25): no analysis that would only wait for an account."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock

import pytest

from embry0.services.account_waits import AccountWait
from embry0.storage.repositories.watcher_runs import ANALYSED_ACTIONS, VERDICT_ACTIONS, WATCHER_ACTIONS
from tests.unit.services.test_watcher import _service

LINES = ["[api] boom", "[api] boom", "[api] boom", "[api] boom"]
WAIT = AccountWait("anthropic", "quota_exhausted", datetime.now(UTC) + timedelta(hours=15), watch=True)


def test_the_skip_is_a_known_action_that_neither_analyses_nor_judges() -> None:
    assert "skipped_account_paused" in WATCHER_ACTIONS
    assert "skipped_account_paused" not in ANALYSED_ACTIONS, "the window stays for the next tick"
    assert "skipped_account_paused" not in VERDICT_ACTIONS


@pytest.mark.asyncio
async def test_a_tick_whose_analysis_would_wait_starts_no_job() -> None:
    svc = _service(lines=LINES)
    svc._executor.account_wait_before_start = AsyncMock(return_value=WAIT)
    out = await svc.run_once()
    assert out["action"] == "skipped_account_paused"
    svc._jobs.create.assert_not_awaited()
    detail = svc._runs.create.await_args.kwargs["detail"]
    assert "anthropic account is quota exhausted" in detail and "2026" not in detail
    asked_repo, asked_config = svc._executor.account_wait_before_start.await_args.args
    assert asked_config["budget_overrun_mode"] == "hard", "the question is about the job the tick would start"


@pytest.mark.asyncio
async def test_a_tick_with_a_ready_account_analyses_as_before() -> None:
    job = {"job_id": "job-w1", "status": "completed", "result_summary": "NO_ISSUE"}
    svc = _service(lines=LINES, job_result=job)
    svc._executor.account_wait_before_start = AsyncMock(return_value=None)
    assert (await svc.run_once())["action"] == "no_issue"


@pytest.mark.asyncio
async def test_an_analysis_parked_mid_run_is_cancelled_and_the_tick_says_why() -> None:
    parked = {"job_id": "job-w1", "status": "paused", "account_wait": WAIT.record(), "total_cost_usd": 0.0}
    svc = _service(lines=LINES, job_result=parked)
    svc._executor.cancel_job = AsyncMock()
    out = await svc.run_once()
    assert out["action"] == "skipped_account_paused"
    svc._executor.cancel_job.assert_awaited_once()
    assert svc._executor.cancel_job.await_args.args == ("job-w1",)
