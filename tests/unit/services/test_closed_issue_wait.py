"""A job whose issue was closed while it waited for answers is not resumed, and never reopens the issue (#518).

A tracker's ``closed`` webhook moved an ``awaiting_input`` issue to ``closed``
and left its job waiting. Once the job's answers were in, its own answer route
or the startup pass (``resume_answered_waits``, #504) resumed it; ``resume()``
could not write the closed issue to ``triaging`` and failed, and
``_fail_resume`` wrote the issue back to ``open``. Now the job is cancelled
instead, with a reason, and a job that ends never reopens a closed or
cancelled issue; the close itself ends the wait (``end_waits``).
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from httpx import ASGITransport, AsyncClient

from embry0.services import issue_executor
from embry0.services.issue_executor import IssueExecutor
from embry0.storage.repositories.issue_inputs import IssueInputsRepository
from tests.unit.api.v1.test_answer_claims_the_wait import HEADERS, _app, _issue_executor, _waiting_issue

pytestmark = pytest.mark.requires_postgres


async def _closed_while_waiting(db: Any, status: str = "closed") -> tuple[Any, Any, str, str, str]:
    """An issue whose job waits for its one question, answered; then the issue was closed (or cancelled)."""
    issues, jobs, issue_id, job_id = await _waiting_issue(db)
    inputs = IssueInputsRepository(db)
    input_id = await inputs.create(issue_id, job_id, "Which clock?", asking_node="developer")
    if status == "closed":
        await issues.update(issue_id, status="open")
    await issues.update(issue_id, status=status)
    return issues, jobs, issue_id, job_id, input_id


def _cancelling_executor(issues: Any, jobs: Any, db: Any) -> IssueExecutor:
    """The answer-path executor of the #499 tests, able to cancel a job for real (no sandbox, no checkpoint)."""
    executor = _issue_executor(issues, jobs)
    executor._inputs = IssueInputsRepository(db)
    executor._tasks_by_job = {}
    executor._background_tasks = set()
    executor._event_bus = None
    executor._database_url = "postgres://unused"
    executor._audit_log_path = None
    executor._db = db
    executor._sandbox = None
    executor._cleanup_job_volumes = AsyncMock()  # type: ignore[method-assign]
    executor._finish_credential_uses = AsyncMock()  # type: ignore[method-assign]
    executor._announce_finished = AsyncMock()  # type: ignore[method-assign]
    return executor


@pytest.fixture(autouse=True)
def _no_checkpoint_or_audit(monkeypatch: pytest.MonkeyPatch) -> Any:
    import embry0.orchestration.checkpoint as checkpoint

    monkeypatch.setattr(checkpoint, "purge_thread", AsyncMock())
    with patch.object(issue_executor, "emit_audit", new=AsyncMock()):
        yield


def _end_waits(executor: IssueExecutor) -> Any:
    end_waits = getattr(executor, "end_waits", None)
    if end_waits is None:
        pytest.fail("nothing ends the waits of an issue that was closed")
    return end_waits


async def _ended(jobs: Any, issues: Any, job_id: str, issue_id: str) -> tuple[str, str, str]:
    job = await jobs.get(job_id) or {}
    return (
        str(job.get("status")),
        str(job.get("error_message") or ""),
        str((await issues.get(issue_id) or {})["status"]),
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("status", ["closed", "cancelled"])
async def test_the_startup_pass_cancels_a_job_whose_issue_ended_while_it_waited(
    db_with_migrations: Any, status: str
) -> None:
    issues, jobs, issue_id, job_id, input_id = await _closed_while_waiting(db_with_migrations, status)
    await IssueInputsRepository(db_with_migrations).answer(input_id, answer="monotonic", answered_by="ada")
    executor = _cancelling_executor(issues, jobs, db_with_migrations)

    resumed = await executor.resume_answered_waits()

    assert resumed == [] and executor.schedule_resume.call_count == 0, (  # type: ignore[attr-defined]
        f"a job of a {status} issue was resumed at startup: {resumed}"
    )
    job_status, reason, issue_status = await _ended(jobs, issues, job_id, issue_id)
    assert (job_status, issue_status) == ("cancelled", status), (job_status, issue_status)
    assert f"Its issue was {status} while it waited for answers" in reason, reason


@pytest.mark.asyncio
async def test_an_answer_to_a_closed_issues_job_cancels_the_job_and_leaves_the_issue_closed(
    db_with_migrations: Any,
) -> None:
    issues, jobs, issue_id, job_id, input_id = await _closed_while_waiting(db_with_migrations)
    executor = _cancelling_executor(issues, jobs, db_with_migrations)
    app = _app(jobs, IssueInputsRepository(db_with_migrations), executor)
    app.state.issues_repo = issues

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post(
            f"/api/v1/jobs/{job_id}/inputs/{input_id}/answer", json={"answer": "monotonic"}, headers=HEADERS
        )

    assert response.status_code == 200, response.text
    assert executor.schedule_resume.call_count == 0, "the job of a closed issue was resumed"  # type: ignore[attr-defined]
    job_status, _reason, issue_status = await _ended(jobs, issues, job_id, issue_id)
    assert (job_status, issue_status) == ("cancelled", "closed"), (job_status, issue_status)


@pytest.mark.asyncio
async def test_closing_an_issue_cancels_its_job_that_waits_for_answers_and_no_other(db_with_migrations: Any) -> None:
    issues, jobs, issue_id, job_id, _input_id = await _closed_while_waiting(db_with_migrations)
    running = await jobs.create(repo="acme/app", task="Another run", issue_id=issue_id)
    await jobs.update(running, status="running")
    executor = _cancelling_executor(issues, jobs, db_with_migrations)

    end_waits = getattr(executor, "end_waits", None)
    if end_waits is None:
        pytest.fail("nothing ends the answer waits of an issue that was closed")
    ended = await end_waits(issue_id)

    assert ended == [job_id], f"the close ended {ended}"
    assert (await jobs.get(job_id) or {})["status"] == "cancelled"
    assert (await jobs.get(running) or {})["status"] == "running", "a job that does not wait for answers was cancelled"
    assert (await issues.get(issue_id) or {})["status"] == "closed", "cancelling the job reopened the issue"
    assert await end_waits(issue_id) == [], "a wait was ended twice"


@pytest.mark.asyncio
async def test_a_resume_that_fails_never_reopens_a_closed_issue(db_with_migrations: Any) -> None:
    """The review's run: a resume of a closed issue's job raises on the issue's first write, and failing it reopened
    the issue."""
    issues, jobs, issue_id, job_id, _input_id = await _closed_while_waiting(db_with_migrations)
    executor = _cancelling_executor(issues, jobs, db_with_migrations)
    executor._open_fallback_pr = AsyncMock(return_value=(None, None))  # type: ignore[method-assign]
    executor._cleanup_sandbox = AsyncMock()  # type: ignore[method-assign]
    executor._traces = None
    await jobs.update(job_id, status="running")

    await executor._fail_resume(issue_id, job_id, ValueError("closed -> triaging"), None, {})

    assert (await jobs.get(job_id) or {})["status"] == "failed"
    assert (await issues.get(issue_id) or {})["status"] == "closed", "a failed resume reopened a closed issue"


@pytest.mark.asyncio
@pytest.mark.parametrize(("issue_status", "after"), [("closed", "closed"), ("in_progress", "open")])
async def test_cancelling_a_job_resets_its_issue_unless_the_issue_ended(
    db_with_migrations: Any, issue_status: str, after: str
) -> None:
    issues, jobs, issue_id, job_id, _input_id = await _closed_while_waiting(db_with_migrations)
    if issue_status != "closed":
        await issues.update(issue_id, status="open")
        await issues.update(issue_id, status=issue_status)
    executor = _cancelling_executor(issues, jobs, db_with_migrations)

    await executor.cancel_job(job_id)

    assert (await issues.get(issue_id) or {})["status"] == after, f"cancelling a job of a {issue_status} issue"


@pytest.mark.asyncio
async def test_ending_waits_leaves_a_job_being_resumed_and_an_issue_still_open(db_with_migrations: Any) -> None:
    from tests.unit.api.v1.test_answer_claims_the_wait import _claim

    issues, jobs, issue_id, job_id, _input_id = await _closed_while_waiting(db_with_migrations)
    executor = _cancelling_executor(issues, jobs, db_with_migrations)
    assert await _claim(jobs)(job_id) is True  # an answer took the job since the close's lookup
    stale = [{"job_id": job_id, "issue_id": issue_id, "status": "awaiting_input"}]
    jobs.list_all = AsyncMock(return_value=(stale, 1))  # type: ignore[method-assign]

    assert await _end_waits(executor)(issue_id) == [], "a job an answer was resuming was cancelled"
    assert (await jobs.get(job_id) or {})["status"] == "running"

    waiting, still_jobs, open_issue, open_job = await _waiting_issue(db_with_migrations)
    other = _cancelling_executor(waiting, still_jobs, db_with_migrations)
    assert await _end_waits(other)(open_issue) == [], "the waits of an issue nobody closed were ended"
    assert (await still_jobs.get(open_job) or {})["status"] == "awaiting_input"


@pytest.mark.asyncio
async def test_a_wait_whose_cancel_fails_is_given_back_not_left_taken(db_with_migrations: Any) -> None:
    issues, jobs, issue_id, job_id, _input_id = await _closed_while_waiting(db_with_migrations)
    executor = _cancelling_executor(issues, jobs, db_with_migrations)
    executor.cancel_job = AsyncMock(side_effect=RuntimeError("the sandbox daemon is gone"))  # type: ignore[method-assign]

    with pytest.raises(RuntimeError):
        await _end_waits(executor)(issue_id)

    assert (await jobs.get(job_id) or {})["status"] == "awaiting_input", (
        "a job whose cancel failed was left running with nothing running it"
    )


async def _post(app: Any, url: str, body: dict[str, Any]) -> Any:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        return await client.post(url, json=body, headers=HEADERS)


@pytest.mark.asyncio
@pytest.mark.parametrize("route", ["the job's", "the issue's"])
async def test_an_answer_to_a_job_that_ended_is_refused_and_says_what_to_do(
    db_with_migrations: Any, route: str
) -> None:
    """#521: the review's case: the issue closed (its waiting job cancelled), then reopened, then the question answered.
    The answer was taken (200) and did nothing."""
    issues, jobs, issue_id, job_id, input_id = await _closed_while_waiting(db_with_migrations)
    executor = _cancelling_executor(issues, jobs, db_with_migrations)
    # The close cancelled its waiting job (as end_waits does), and the issue was reopened: a new run starts, not this.
    await db_with_migrations.execute("UPDATE jobs SET status = 'cancelled' WHERE job_id = $1", job_id)
    await issues.update(issue_id, status="open")
    app = _app(jobs, IssueInputsRepository(db_with_migrations), executor)
    app.state.issues_repo = issues
    url = (
        f"/api/v1/jobs/{job_id}/inputs/{input_id}/answer"
        if route == "the job's"
        else f"/api/v1/issues/{issue_id}/inputs/{input_id}/answer"
    )

    response = await _post(app, url, {"answer": "monotonic"})

    stored = (await IssueInputsRepository(db_with_migrations).get(input_id) or {}).get("status")
    assert (response.status_code, stored) == (409, "pending"), (
        f"an answer to an ended job on {route} route: {response.status_code} {response.text}, the question {stored}"
    )
    detail = response.json()["detail"]
    assert f"Job {job_id} has ended (cancelled)" in detail, detail
    # Reopening the issue starts nothing on GitHub or GitLab: the message says what does (review of #535).
    assert "runs a new job" not in detail, f"the refusal promises a run a reopen does not start: {detail}"
    assert f"POST /api/v1/jobs/{job_id}/run-again" in detail and "re-add the trigger label `embry0`" in detail, detail


@pytest.mark.asyncio
@pytest.mark.parametrize("parked", [False, True], ids=["paused", "parked for its account"])
async def test_closing_an_issue_cancels_its_paused_job(db_with_migrations: Any, parked: bool) -> None:
    issues, jobs, issue_id, job_id, _input_id = await _closed_while_waiting(db_with_migrations)
    await db_with_migrations.execute("UPDATE jobs SET status = 'paused' WHERE job_id = $1", job_id)
    if parked:
        await db_with_migrations.execute(
            """UPDATE jobs SET account_wait = '{"provider": "anthropic", "reason": "usage", "until": null}'::jsonb
               WHERE job_id = $1""",
            job_id,
        )
    executor = _cancelling_executor(issues, jobs, db_with_migrations)

    ended = await _end_waits(executor)(issue_id)

    job_status, reason, issue_status = await _ended(jobs, issues, job_id, issue_id)
    assert (ended, job_status, issue_status) == ([job_id], "cancelled", "closed"), (ended, job_status, issue_status)
    assert "Its issue was closed while it was paused" in reason, reason


@pytest.mark.asyncio
@pytest.mark.parametrize("choice", ["continue_retrying", "merge_as_is"])
async def test_the_resume_route_refuses_a_paused_job_of_a_closed_issue(db_with_migrations: Any, choice: str) -> None:
    issues, jobs, issue_id, job_id, _input_id = await _closed_while_waiting(db_with_migrations)
    await db_with_migrations.execute("UPDATE jobs SET status = 'paused' WHERE job_id = $1", job_id)
    executor = _cancelling_executor(issues, jobs, db_with_migrations)
    app = _app(jobs, IssueInputsRepository(db_with_migrations), executor)
    app.state.issues_repo = issues

    response = await _post(app, f"/api/v1/jobs/{job_id}/resume", {"choice": choice})

    assert response.status_code == 409, f"{choice} on a paused job of a closed issue: {response.status_code}"
    detail = response.json()["detail"]
    assert "issue is closed, so the job cannot go on" in detail and "Stop it to release its sandbox" in detail, detail
    assert "runs a new job" not in detail and f"/api/v1/jobs/{job_id}/run-again" in detail, (
        f"the refusal does not say what starts a new run: {detail}"
    )
    assert executor.schedule_resume.call_count == 0  # type: ignore[attr-defined]
    assert (await jobs.get(job_id) or {})["status"] == "paused", "a refused resume changed the job"


@pytest.mark.asyncio
async def test_a_paused_job_of_a_closed_issue_can_still_be_stopped(db_with_migrations: Any) -> None:
    issues, jobs, issue_id, job_id, _input_id = await _closed_while_waiting(db_with_migrations)
    await db_with_migrations.execute("UPDATE jobs SET status = 'paused' WHERE job_id = $1", job_id)
    executor = _cancelling_executor(issues, jobs, db_with_migrations)
    app = _app(jobs, IssueInputsRepository(db_with_migrations), executor)
    app.state.issues_repo = issues

    response = await _post(app, f"/api/v1/jobs/{job_id}/resume", {"choice": "abandon"})

    assert (response.status_code, response.json().get("status")) == (200, "cancelled"), (
        f"stopping a paused job of a closed issue did not cancel it: {response.text}"
    )
    job_status, _reason, issue_status = await _ended(jobs, issues, job_id, issue_id)
    assert (job_status, issue_status) == ("cancelled", "closed"), (job_status, issue_status)


@pytest.mark.asyncio
async def test_the_resume_route_refuses_a_parked_job_of_a_closed_issue(db_with_migrations: Any) -> None:
    """A job parked for its account goes on with "Try now"; not once its issue is closed."""
    issues, jobs, issue_id, job_id, _input_id = await _closed_while_waiting(db_with_migrations)
    await db_with_migrations.execute(
        """UPDATE jobs SET status = 'paused',
           account_wait = '{"provider": "anthropic", "reason": "usage", "until": null}'::jsonb WHERE job_id = $1""",
        job_id,
    )
    executor = _cancelling_executor(issues, jobs, db_with_migrations)
    executor.go_on_after_account_wait = MagicMock()  # type: ignore[method-assign]
    app = _app(jobs, IssueInputsRepository(db_with_migrations), executor)
    app.state.issues_repo = issues

    response = await _post(app, f"/api/v1/jobs/{job_id}/resume", {"choice": "continue_retrying"})

    assert response.status_code == 409, f"Try now on a parked job of a closed issue: {response.status_code}"
    executor.go_on_after_account_wait.assert_not_called()


@pytest.mark.asyncio
async def test_an_inbound_reply_to_a_question_of_a_job_that_ended_is_not_taken(db_with_migrations: Any) -> None:
    """The review's case: a GitHub `/answer` to a cancelled job's question was applied (`applied=1 input=answered`)."""
    import inspect

    from embry0.notifications.inbound_dispatch import apply_inbound_directives

    if "jobs_repo" not in inspect.signature(apply_inbound_directives).parameters:
        pytest.fail("the inbound dispatcher cannot ask whether a question's job ended")
    issues, jobs, issue_id, job_id, input_id = await _closed_while_waiting(db_with_migrations)
    await db_with_migrations.execute("UPDATE jobs SET status = 'cancelled' WHERE job_id = $1", job_id)
    await issues.update(issue_id, status="open")
    inputs = IssueInputsRepository(db_with_migrations)
    resumed = AsyncMock()

    result = await apply_inbound_directives(
        issue_id=issue_id,
        directives=[(input_id, "answer", "monotonic")],
        inputs_repo=inputs,
        on_all_answered=resumed,
        answered_by="github:ada",
        jobs_repo=jobs,
    )

    stored = (await inputs.get(input_id) or {}).get("status")
    assert (result.applied, getattr(result, "job_ended", None), stored) == (0, 1, "pending"), (
        f"an inbound answer to an ended job: {result}, the question {stored}"
    )
