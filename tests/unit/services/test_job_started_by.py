"""Whom a job's question or pause goes to: the person who started it, when a person did."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from embry0.services._executor_outcomes import person_who_started
from embry0.services._executor_run import _announce_paused


def test_a_person_started_it_only_when_an_email_did() -> None:
    assert person_who_started({"dispatched_by": "ada@example.com"}) == "ada@example.com"
    assert person_who_started({"dispatched_by": "embry0:master-key"}) is None
    assert person_who_started({"dispatched_by": "octocat"}) is None
    assert person_who_started({"dispatched_by": None}) is None
    assert person_who_started(None) is None


def _executor(**over: Any) -> Any:
    return SimpleNamespace(
        _webhook_notifier=MagicMock(),
        _jobs=SimpleNamespace(
            get=AsyncMock(return_value={"repo": "acme/web", "task": "Do it", "dispatched_by": "ada@example.com"})
        ),
        _issues=SimpleNamespace(get=AsyncMock(return_value={"title": "Fix the thing"})),
        _config=SimpleNamespace(dashboard_public_url="https://e0.example/"),
        **over,
    )


@pytest.mark.asyncio
async def test_a_pause_is_announced_with_who_started_the_job() -> None:
    executor = _executor()
    await _announce_paused(executor, "iss-1", "job-1", "max_retries")
    event, data = executor._webhook_notifier.emit_soon.call_args.args
    assert event == "job.paused"
    assert data == {
        "job_id": "job-1",
        "issue_id": "iss-1",
        "repo": "acme/web",
        "title": "Fix the thing",
        "reason": "max_retries",
        "started_by": "ada@example.com",
        "url": "https://e0.example/jobs/job-1",
    }


@pytest.mark.asyncio
async def test_a_job_with_no_issue_names_its_task_and_no_notifier_is_no_announcement() -> None:
    executor = _executor()
    await _announce_paused(executor, None, "job-1", "max_retries")
    assert executor._webhook_notifier.emit_soon.call_args.args[1]["title"] == "Do it"
    quiet = _executor()
    quiet._webhook_notifier = None
    await _announce_paused(quiet, None, "job-1", "max_retries")
