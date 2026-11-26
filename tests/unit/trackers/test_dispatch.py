"""create_tracked_issue / start_triage — the shared trigger-side steps (#160)."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock

import pytest

from embry0.storage.repositories.jobs import StatusTransitionConflict
from embry0.trackers import TrackerIssue, create_tracked_issue, dispatch_open_issue, start_triage


class _FakeIssues:
    def __init__(self, fail_update: bool = False) -> None:
        self.created: dict[str, Any] | None = None
        self.updates: list[dict[str, Any]] = []
        self._fail_update = fail_update

    async def create(self, **kw: Any) -> str:
        self.created = kw
        return "iss-1"

    async def update(self, issue_id: str, **kw: Any) -> None:
        if self._fail_update and kw.get("status") == "triaging":
            raise StatusTransitionConflict("conflict")
        self.updates.append({"issue_id": issue_id, **kw})


def _issue(tracker: str, **kw: Any) -> TrackerIssue:
    defaults: dict[str, Any] = {
        "tracker": tracker,
        "key": "7",
        "title": "t",
        "body": "b",
        "url": "https://x/7",
        "labels": ["embry0"],
        "repo": "o/r",
    }
    defaults.update(kw)
    return TrackerIssue(**defaults)


@pytest.mark.asyncio
async def test_github_rows_get_the_generic_tracker_columns():
    repo = _FakeIssues()
    await create_tracked_issue(repo, _issue("github"), created_by="webhook")
    assert repo.created is not None and repo.created["github_sync_enabled"] is True
    assert repo.created["tracker"] == "github"
    assert repo.created["tracker_key"] == "7"
    assert repo.created["tracker_url"] == "https://x/7"
    assert repo.created["tracker_native_id"] is None


@pytest.mark.asyncio
async def test_linear_rows_get_the_generic_tracker_columns():
    repo = _FakeIssues()
    await create_tracked_issue(repo, _issue("linear", key="ENG-9", native_id="uuid-9"), created_by="linear-webhook")
    assert repo.created is not None and "github_sync_enabled" not in repo.created
    assert repo.created["tracker"] == "linear"
    assert repo.created["tracker_key"] == "ENG-9"
    assert repo.created["tracker_native_id"] == "uuid-9"


@pytest.mark.asyncio
async def test_other_trackers_get_the_generic_tracker_columns():
    repo = _FakeIssues()
    await create_tracked_issue(repo, _issue("gitlab"), created_by="gitlab-webhook")
    assert repo.created["tracker"] == "gitlab"
    assert repo.created["tracker_key"] == "7"
    assert repo.created["tracker_url"] == "https://x/7"


@pytest.mark.asyncio
async def test_start_triage_only_dispatches_from_open():
    repo = _FakeIssues()
    executor = AsyncMock()
    ok = await start_triage("iss-1", "in_progress", repo, executor, tracker="github", reason="labeled")
    assert ok is False
    executor.execute.assert_not_called()


@pytest.mark.asyncio
async def test_start_triage_reverts_to_open_when_dispatch_fails():
    repo = _FakeIssues()
    executor = AsyncMock()
    executor.execute = AsyncMock(side_effect=RuntimeError("boom"))
    ok = await start_triage("iss-1", "open", repo, executor, tracker="gitlab", reason="labeled")
    assert ok is False
    assert repo.updates[-1]["status"] == "open"


@pytest.mark.asyncio
async def test_start_triage_lost_race_is_not_a_dispatch():
    repo = _FakeIssues(fail_update=True)
    executor = AsyncMock()
    ok = await start_triage("iss-1", "open", repo, executor, tracker="linear", reason="labeled")
    assert ok is False
    executor.execute.assert_not_called()


@pytest.mark.asyncio
async def test_start_triage_dispatches_and_reports_true():
    repo = _FakeIssues()
    executor = AsyncMock()
    executor.execute = AsyncMock(return_value="job-1")
    ok = await start_triage("iss-1", "open", repo, executor, tracker="github", reason="opened_with_label")
    assert ok is True
    assert repo.updates[0]["status"] == "triaging"


@pytest.mark.asyncio
async def test_start_triage_carries_the_actor_to_the_job_row():
    """#246: the dispatch rate limit buckets on the human the delivery names,
    so the label's applier has to reach ``jobs.dispatched_by``."""
    repo = _FakeIssues()
    executor = AsyncMock()
    executor.execute = AsyncMock(return_value="job-1")
    await start_triage("iss-1", "open", repo, executor, tracker="github", reason="labeled", actor="octocat")
    executor.execute.assert_awaited_once_with("iss-1", dispatched_by="octocat")


@pytest.mark.asyncio
async def test_start_triage_without_an_actor_dispatches_anonymously():
    """No named actor must mean None, not "" — the gate skips the rate limit
    for unattributable jobs instead of pooling them in one bucket."""
    repo = _FakeIssues()
    executor = AsyncMock()
    executor.execute = AsyncMock(return_value="job-1")
    await start_triage("iss-1", "open", repo, executor, tracker="github", reason="labeled")
    executor.execute.assert_awaited_once_with("iss-1", dispatched_by=None)


@pytest.mark.asyncio
async def test_dispatch_open_issue_returns_the_job_it_started():
    """The case-accept route hands the job id back; the webhooks only need
    the bool, which start_triage still returns."""
    repo = _FakeIssues()
    executor = AsyncMock()
    executor.execute = AsyncMock(return_value="job-42")
    job_id = await dispatch_open_issue("iss-1", "open", repo, executor, tracker="github", reason="case_accepted")
    assert job_id == "job-42"
    executor.execute.assert_awaited_once_with("iss-1", dispatched_by=None)
    assert await dispatch_open_issue("iss-1", "triaging", repo, executor, tracker="github", reason="x") is None


@pytest.mark.asyncio
async def test_a_removed_organization_s_issue_goes_back_to_open_and_a_person_s_dispatch_is_told_why():
    """#721: the job store refuses the job. A label trigger (GitHub, GitLab) answers False, quietly, the issue back in
    `open`; the routes a person asked (accepting a case or a lead) get the refusal, the issue back in `open` too."""
    from embry0.storage.repositories.jobs import OrganizationRemoved

    for tracker in ("github", "gitlab"):
        repo = _FakeIssues()
        executor = AsyncMock()
        executor.execute = AsyncMock(side_effect=OrganizationRemoved("acme"))
        assert await start_triage("iss-1", "open", repo, executor, tracker=tracker, reason="labeled") is False
        assert [u["status"] for u in repo.updates] == ["triaging", "open"]

    repo = _FakeIssues()
    executor = AsyncMock()
    executor.execute = AsyncMock(side_effect=OrganizationRemoved("acme"))
    with pytest.raises(OrganizationRemoved):
        await dispatch_open_issue("iss-1", "open", repo, executor, tracker="github", reason="case_accepted")
    assert [u["status"] for u in repo.updates] == ["triaging", "open"]
