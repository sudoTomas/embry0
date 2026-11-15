"""RAV-1064: public IssueExecutor interface — submit_job / schedule_resume /
resume_or_execute.

These are the promoted entry points for callers that previously reached past
the underscore boundary (POST /jobs, the answer flows, the log watcher,
PR-comment dispatch). The tests pin the delegation contract: what each public
method passes to ``_run_workflow`` / ``resume`` / ``_track_task``.
"""

from unittest.mock import AsyncMock, MagicMock

import pytest

from embry0.services.issue_executor import IssueExecutor


def _bare_executor():
    ex = IssueExecutor.__new__(IssueExecutor)
    ex._jobs = MagicMock()
    ex._issues = MagicMock()
    ex._issues.update = AsyncMock()
    # #499: the issue is taken before anything goes on with it.
    ex._issues.claim_answered = AsyncMock(return_value=True)
    ex._issues.get = AsyncMock(return_value={"status": "triaging"})
    tracked = []

    def _fake_track(coro, **kwargs):
        tracked.append(kwargs)
        coro.close()
        return MagicMock()

    ex._track_task = _fake_track  # type: ignore[method-assign]
    return ex, tracked


@pytest.mark.asyncio
async def test_submit_job_builds_synthetic_issue_and_dispatches():
    ex, tracked = _bare_executor()
    captured = {}

    def _fake_run(issue_id, job_id, issue, additional_context=""):
        captured.update(issue_id=issue_id, job_id=job_id, issue=issue, additional_context=additional_context)

        async def _noop():
            return None

        return _noop()

    ex._run_workflow = _fake_run  # type: ignore[method-assign]

    ex.submit_job(
        "job-1",
        repo="o/r",
        title="Fix it",
        body="details",
        github_number=7,
        additional_context="ctx",
        kind="watcher_analysis",
    )

    assert captured["issue_id"] is None
    assert captured["job_id"] == "job-1"
    assert captured["issue"] == {
        "repo": "o/r",
        "title": "Fix it",
        "body": "details",
        "tracker": "github",
        "tracker_key": "7",
    }
    assert captured["additional_context"] == "ctx"
    assert tracked == [
        {
            "kind": "watcher_analysis",
            "job_id": "job-1",
            # #307: what a restart needs to start the job again.
            "launch": {
                "runner": "workflow",
                "issue_id": None,
                "issue": captured["issue"],
                "additional_context": "ctx",
                "kind": "watcher_analysis",
            },
        }
    ]


@pytest.mark.asyncio
async def test_submit_job_defaults():
    ex, tracked = _bare_executor()
    captured = {}

    def _fake_run(issue_id, job_id, issue, additional_context=""):
        captured.update(issue=issue, additional_context=additional_context)

        async def _noop():
            return None

        return _noop()

    ex._run_workflow = _fake_run  # type: ignore[method-assign]

    ex.submit_job("job-2", repo=None, title="t")

    assert captured["issue"] == {"repo": None, "title": "t", "body": "", "tracker": None, "tracker_key": None}
    assert captured["additional_context"] == ""
    assert tracked[0]["kind"] == "workflow_execute"


@pytest.mark.asyncio
async def test_schedule_resume_delegates_to_tracked_resume():
    ex, tracked = _bare_executor()
    captured = {}

    def _fake_resume(issue_id, job_id, answers, claimed=None, claimed_issue=None):
        captured.update(issue_id=issue_id, job_id=job_id, answers=answers, claimed=claimed)

        async def _noop():
            return None

        return _noop()

    ex.resume = _fake_resume  # type: ignore[method-assign]

    ex.schedule_resume("iss-1", "job-1", {"choice": "retry"}, kind="resume_via_api")

    assert captured == {"issue_id": "iss-1", "job_id": "job-1", "answers": {"choice": "retry"}, "claimed": None}
    assert tracked == [{"kind": "resume_via_api", "job_id": "job-1", "issue_id": "iss-1", "run_slot": False}]
    # A route that took the pause itself says so (#470).
    ex.schedule_resume("iss-1", "job-1", {"choice": "retry"}, kind="resume_via_api", claimed="paused")
    assert captured["claimed"] == "paused"


@pytest.mark.asyncio
async def test_resume_or_execute_resumes_latest_awaiting_job():
    ex, tracked = _bare_executor()
    ex._jobs.list_all = AsyncMock(
        return_value=(
            [
                {"job_id": "job-new", "status": "awaiting_input"},
                {"job_id": "job-done", "status": "completed"},
                {"job_id": "job-old", "status": "awaiting_input"},
            ],
            3,
        )
    )
    captured = {}

    def _fake_resume(issue_id, job_id, answers, claimed=None, claimed_issue=None):
        captured.update(issue_id=issue_id, job_id=job_id, answers=answers, claimed=claimed)

        async def _noop():
            return None

        return _noop()

    ex.resume = _fake_resume  # type: ignore[method-assign]
    ex.execute = AsyncMock()  # type: ignore[method-assign]
    ex._jobs.claim_answer_wait = AsyncMock(return_value=True)

    await ex.resume_or_execute("iss-1", "Q: q\nA: a")

    # list_all orders created_at DESC — the first awaiting job is the latest.
    assert captured == {"issue_id": "iss-1", "job_id": "job-new", "answers": "Q: q\nA: a", "claimed": "awaiting_input"}
    ex._jobs.claim_answer_wait.assert_awaited_once_with("job-new")
    assert tracked[0]["kind"] == "resume_via_answer"
    ex.execute.assert_not_awaited()
    ex._issues.update.assert_not_awaited()


@pytest.mark.asyncio
async def test_resume_or_execute_starts_fresh_with_context_when_no_awaiting_job():
    ex, tracked = _bare_executor()
    ex._jobs.list_all = AsyncMock(return_value=([{"job_id": "job-done", "status": "completed"}], 1))
    ex.execute = AsyncMock(return_value="job-fresh")  # type: ignore[method-assign]

    await ex.resume_or_execute("iss-1", "Q: q\nA: a")

    # #499: taking the issue (awaiting_input to triaging) is what flips it, before the fresh run.
    ex._issues.claim_answered.assert_awaited_once_with("iss-1")
    ex._issues.update.assert_not_awaited()
    # The accumulated Q&A must ride the fresh run (execute's retriage contract).
    # #281: no named person on this channel — the service actor asked.
    ex.execute.assert_awaited_once_with("iss-1", additional_context="Q: q\nA: a", dispatched_by="embry0:answer-resume")
    assert tracked == []


@pytest.mark.asyncio
async def test_resume_or_execute_fresh_run_carries_the_person_who_answered():
    ex, _tracked = _bare_executor()
    ex._jobs.list_all = AsyncMock(return_value=([], 0))
    ex.execute = AsyncMock(return_value="job-fresh")  # type: ignore[method-assign]

    await ex.resume_or_execute("iss-1", "ctx", dispatched_by="alex@example.com")

    assert ex.execute.await_args.kwargs["dispatched_by"] == "alex@example.com"


@pytest.mark.asyncio
async def test_resume_or_execute_rolls_back_to_open_on_execute_failure():
    ex, _tracked = _bare_executor()
    ex._jobs.list_all = AsyncMock(return_value=([], 0))
    ex.execute = AsyncMock(side_effect=RuntimeError("boom"))  # type: ignore[method-assign]

    await ex.resume_or_execute("iss-1", "")

    statuses = [c.kwargs.get("status") for c in ex._issues.update.await_args_list]
    ex._issues.claim_answered.assert_awaited_once_with("iss-1")
    assert statuses == ["open"]
