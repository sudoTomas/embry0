"""Answers that each find a job's last question answered resume it once (#499).

``POST /jobs/{job_id}/inputs/{input_id}/answer`` read the input, refused it
only if it was already ``answered``, recorded the answer, and, with no blocking
question left, scheduled a resume when the job read ``awaiting_input``.
Neither step was taken in one statement, so two answers given at once (a
double click, a retry, two people) both got 200 and two resumes of the same
checkpoint were scheduled. The issue paths (``resume_or_execute``,
``resume_for_issue``) had the same shape. Now an answer is taken once
(``IssueInputsRepository.answer``) and a job is taken from its wait once
(``JobsRepository.claim_answer_wait``, through ``IssueExecutor.resume_answered``).

The races are made deterministic: both requests read the input before either
writes.
"""

from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from httpx import ASGITransport, AsyncClient

from embry0.api.app import create_app
from embry0.config import Embry0Config
from embry0.services.issue_executor import IssueExecutor
from embry0.storage.repositories.issue_inputs import IssueInputsRepository
from embry0.storage.repositories.jobs import JobsRepository

pytestmark = pytest.mark.requires_postgres

HEADERS = {"X-Requested-With": "XMLHttpRequest"}


class _BothRead(IssueInputsRepository):
    """The inputs repository, with each request's first read of an input held until both requests have made it."""

    def __init__(self, db: Any) -> None:
        super().__init__(db)
        self.barrier, self.held = asyncio.Barrier(2), 0

    async def get(self, input_id: str) -> Any:
        row = await super().get(input_id)
        if self.held < 2:
            self.held += 1
            await asyncio.wait_for(self.barrier.wait(), timeout=5)
        return row


def _claim(jobs: JobsRepository) -> Any:
    claim = getattr(jobs, "claim_answer_wait", None)
    if claim is None:
        pytest.fail("the jobs repository has no way to take a job from its answer wait for one resume")
    return claim


def _resume_answered(executor: IssueExecutor) -> Any:
    resume = getattr(executor, "resume_answered", None)
    if resume is None:
        pytest.fail("the executor has no resume that takes the job from its answer wait first")
    return resume


def _executor(jobs: JobsRepository) -> IssueExecutor:
    """An executor whose resumes are recorded rather than run, with the real claim."""
    ex = IssueExecutor.__new__(IssueExecutor)
    ex._jobs = jobs
    ex.schedule_resume = MagicMock()  # type: ignore[method-assign]
    return ex


async def _waiting_job(jobs: JobsRepository) -> str:
    job_id = await jobs.create(repo="acme/app", task="Fix the idle timer")
    await jobs.update(job_id, status="running")
    await jobs.update(job_id, status="awaiting_input")
    return job_id


def _app(jobs: JobsRepository, inputs: IssueInputsRepository, executor: IssueExecutor) -> Any:
    app = create_app(Embry0Config(_env_file=None, auth_dev_mode=True, webhook_dev_mode=True))
    app.state.jobs_repo = jobs
    app.state.inputs_repo = inputs
    app.state.issue_executor = executor
    return app


async def _answer_at_once(app: Any, job_id: str, answers: list[tuple[str, str]]) -> list[Any]:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        return list(
            await asyncio.gather(
                *(
                    client.post(f"/api/v1/jobs/{job_id}/inputs/{input_id}/answer", json={"answer": a}, headers=HEADERS)
                    for input_id, a in answers
                )
            )
        )


@pytest.mark.asyncio
async def test_two_answers_to_one_question_at_once_take_one_and_resume_once(db_with_migrations: Any) -> None:
    jobs = JobsRepository(db_with_migrations)
    job_id = await _waiting_job(jobs)
    input_id = await IssueInputsRepository(db_with_migrations).create(
        None, job_id, "Which clock?", asking_node="developer"
    )
    executor = _executor(jobs)
    app = _app(jobs, _BothRead(db_with_migrations), executor)

    first, second = await _answer_at_once(app, job_id, [(input_id, "wall"), (input_id, "monotonic")])

    resumes = executor.schedule_resume.call_count  # type: ignore[attr-defined]
    assert resumes == 1, f"two answers to one question resumed the job {resumes} times: {first.text} / {second.text}"
    statuses = sorted([first.status_code, second.status_code])
    assert statuses == [200, 409], f"one answer is taken and the other refused: {statuses}"
    taken = first if first.status_code == 200 else second
    refused = second if taken is first else first
    assert refused.json()["detail"] == "Input has already been answered", refused.json()
    stored = await IssueInputsRepository(db_with_migrations).get(input_id)
    assert stored is not None and stored["answer"] == taken.json()["answer"], (stored, taken.json())
    assert executor.schedule_resume.call_args.kwargs.get("claimed") == "awaiting_input"  # type: ignore[attr-defined]
    assert (await jobs.get(job_id) or {})["status"] == "running"


@pytest.mark.asyncio
async def test_the_last_two_questions_answered_at_once_resume_the_job_once(db_with_migrations: Any) -> None:
    jobs = JobsRepository(db_with_migrations)
    job_id = await _waiting_job(jobs)
    inputs = IssueInputsRepository(db_with_migrations)
    clock = await inputs.create(None, job_id, "Which clock?", asking_node="developer")
    zone = await inputs.create(None, job_id, "Which time zone?", asking_node="developer")
    executor = _executor(jobs)
    app = _app(jobs, _BothRead(db_with_migrations), executor)

    responses = await _answer_at_once(app, job_id, [(clock, "monotonic"), (zone, "UTC")])

    assert [r.status_code for r in responses] == [200, 200], [r.text for r in responses]
    resumes = executor.schedule_resume.call_count  # type: ignore[attr-defined]
    assert resumes == 1, f"two last answers resumed the job {resumes} times"


@pytest.mark.asyncio
async def test_an_answer_is_taken_once_and_still_overrides_the_orchestrators_own(db_with_migrations: Any) -> None:
    jobs = JobsRepository(db_with_migrations)
    job_id = await _waiting_job(jobs)
    inputs = IssueInputsRepository(db_with_migrations)
    input_id = await inputs.create(None, job_id, "Which clock?", asking_node="developer")
    await db_with_migrations.execute("UPDATE issue_inputs SET status = 'auto_answered' WHERE id = $1", input_id)

    first = await inputs.answer(input_id, answer="monotonic", answered_by="ada")
    second = await inputs.answer(input_id, answer="wall", answered_by="bo")
    assert (first, second) == (True, False), (
        f"one question's answer must be taken once, a person's over the orchestrator's (RAV-1059): {first}, {second}"
    )
    assert (await inputs.get(input_id) or {})["answer"] == "monotonic"


async def _waiting_issue(db: Any) -> tuple[Any, JobsRepository, str, str]:
    """An issue waiting for answers, with its job waiting too."""
    from embry0.storage.repositories.issues import IssuesRepository

    issues, jobs = IssuesRepository(db), JobsRepository(db)
    issue_id = await issues.create("Fix the idle timer", repo="acme/app")
    for status in ("triaging", "in_progress", "awaiting_input"):
        await issues.update(issue_id, status=status)
    job_id = await jobs.create(repo="acme/app", task="Fix the idle timer", issue_id=issue_id)
    await jobs.update(job_id, status="running")
    await jobs.update(job_id, status="awaiting_input")
    return issues, jobs, issue_id, job_id


def _claim_issue(issues: Any) -> Any:
    claim = getattr(issues, "claim_answered", None)
    if claim is None:
        pytest.fail("the issues repository has no way to take an issue from its answer wait")
    return claim


def _issue_executor(issues: Any, jobs: JobsRepository) -> IssueExecutor:
    executor = _executor(jobs)
    executor._issues = issues
    executor.execute = AsyncMock(return_value="job-fresh")  # type: ignore[method-assign]
    return executor


@pytest.mark.asyncio
async def test_an_issue_another_answer_took_is_not_resumed_or_started_again(db_with_migrations: Any) -> None:
    issues, jobs, issue_id, job_id = await _waiting_issue(db_with_migrations)
    executor = _issue_executor(issues, jobs)
    # Another answer took the issue and its job first.
    assert await _claim_issue(issues)(issue_id) is True
    assert await _claim(jobs)(job_id) is True

    await executor.resume_or_execute(issue_id, "Q: which clock?\nA: monotonic")
    await executor.resume_for_issue(issue_id)

    executor.schedule_resume.assert_not_called()  # type: ignore[attr-defined]
    executor.execute.assert_not_awaited()  # type: ignore[attr-defined]


class _CountBoth(IssueInputsRepository):
    """Both requests count the issue's pending questions only once both answers are recorded."""

    def __init__(self, db: Any) -> None:
        super().__init__(db)
        self.barrier = asyncio.Barrier(2)

    async def count_pending_blocking(self, issue_id: str) -> int:
        await asyncio.wait_for(self.barrier.wait(), timeout=5)
        return await super().count_pending_blocking(issue_id)


async def _answer_issue_at_once(app: Any, issue_id: str, answers: list[tuple[str, str]]) -> list[Any]:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        return list(
            await asyncio.gather(
                *(
                    client.post(
                        f"/api/v1/issues/{issue_id}/inputs/{input_id}/answer", json={"answer": a}, headers=HEADERS
                    )
                    for input_id, a in answers
                )
            )
        )


@pytest.mark.asyncio
@pytest.mark.parametrize("ordering", ["the second looks after the first took the job", "both reach the issue at once"])
async def test_an_issues_last_two_answers_at_once_resume_its_job_once_and_start_nothing(
    db_with_migrations: Any, ordering: str
) -> None:
    """Both answers find no question left. The second used to look for the waiting job after the first had taken
    it, find none, and start a fresh run for the issue."""
    issues, jobs, issue_id, job_id = await _waiting_issue(db_with_migrations)
    inputs = _CountBoth(db_with_migrations)
    clock = await inputs.create(issue_id, job_id, "Which clock?", asking_node="developer")
    zone = await inputs.create(issue_id, job_id, "Which time zone?", asking_node="developer")
    executor = _issue_executor(issues, jobs)
    if ordering == "both reach the issue at once":
        gate = asyncio.Barrier(2)
        claim = _claim_issue(issues)

        async def claim_answered(issue: str) -> bool:
            await asyncio.wait_for(gate.wait(), timeout=5)
            return await claim(issue)

        issues.claim_answered = claim_answered  # type: ignore[method-assign]
    else:
        took = asyncio.Event()
        claim_job = jobs.claim_answer_wait

        async def claim_answer_wait(job: str) -> bool:
            won = await claim_job(job)
            took.set()
            return won

        jobs.claim_answer_wait = claim_answer_wait  # type: ignore[method-assign]
        looked = {"n": 0}
        waiting_job = executor.waiting_job

        async def late_waiting_job(issue: str) -> Any:
            looked["n"] += 1
            if looked["n"] == 2:
                await asyncio.wait_for(took.wait(), timeout=5)
            return await waiting_job(issue)

        executor.waiting_job = late_waiting_job  # type: ignore[method-assign]
    app = _app(jobs, inputs, executor)
    app.state.issues_repo = issues

    responses = await _answer_issue_at_once(app, issue_id, [(clock, "monotonic"), (zone, "UTC")])

    statuses = sorted(r.status_code for r in responses)
    resumes = executor.schedule_resume.call_count  # type: ignore[attr-defined]
    fresh = executor.execute.await_count  # type: ignore[attr-defined]
    assert (statuses, resumes, fresh) == ([200, 200], 1, 0), (
        f"{ordering}: statuses {statuses}, the job resumed {resumes} times, {fresh} fresh runs started"
    )
    assert (await issues.get(issue_id) or {})["status"] == "triaging"


@pytest.mark.asyncio
async def test_an_answer_on_the_jobs_route_and_one_on_the_issues_route_resume_once(db_with_migrations: Any) -> None:
    issues, jobs, issue_id, job_id = await _waiting_issue(db_with_migrations)
    inputs = _BothRead(db_with_migrations)
    clock = await inputs.create(issue_id, job_id, "Which clock?", asking_node="developer")
    zone = await inputs.create(issue_id, job_id, "Which time zone?", asking_node="developer")
    executor = _issue_executor(issues, jobs)
    app = _app(jobs, inputs, executor)
    app.state.issues_repo = issues

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        responses = await asyncio.gather(
            client.post(f"/api/v1/jobs/{job_id}/inputs/{clock}/answer", json={"answer": "monotonic"}, headers=HEADERS),
            client.post(f"/api/v1/issues/{issue_id}/inputs/{zone}/answer", json={"answer": "UTC"}, headers=HEADERS),
        )

    resumes = executor.schedule_resume.call_count  # type: ignore[attr-defined]
    fresh = executor.execute.await_count  # type: ignore[attr-defined]
    assert ([r.status_code for r in responses], resumes, fresh) == ([200, 200], 1, 0), (
        f"the two routes resumed the job {resumes} times and started {fresh} fresh runs"
    )


@pytest.mark.asyncio
async def test_a_resume_refused_for_a_shutdown_gives_the_wait_back(db_with_migrations: Any) -> None:
    from embry0.orchestration.restart import ExecutorDraining

    jobs = JobsRepository(db_with_migrations)
    job_id = await _waiting_job(jobs)
    executor = _executor(jobs)
    executor.schedule_resume = MagicMock(side_effect=ExecutorDraining("shutting down"))  # type: ignore[method-assign]

    with pytest.raises(ExecutorDraining):
        await _resume_answered(executor)(None, job_id, "Q: q\nA: a", kind="resume_via_job_answer")

    assert (await jobs.get(job_id) or {})["status"] == "awaiting_input", "a resume that never ran must leave it waiting"


@pytest.mark.asyncio
async def test_two_answers_to_an_issues_question_at_once_take_one_and_resume_once(db_with_migrations: Any) -> None:
    """The issue's answer route: its last answer resumes the issue's waiting job through ``resume_or_execute``."""
    from embry0.storage.repositories.issues import IssuesRepository

    issues, jobs = IssuesRepository(db_with_migrations), JobsRepository(db_with_migrations)
    issue_id = await issues.create("Fix the idle timer", repo="acme/app")
    for status in ("triaging", "in_progress", "awaiting_input"):
        await issues.update(issue_id, status=status)
    job_id = await jobs.create(repo="acme/app", task="Fix the idle timer", issue_id=issue_id)
    await jobs.update(job_id, status="running")
    await jobs.update(job_id, status="awaiting_input")
    input_id = await IssueInputsRepository(db_with_migrations).create(
        issue_id, job_id, "Which clock?", asking_node="developer"
    )
    executor = _executor(jobs)
    executor._issues = issues
    app = _app(jobs, _BothRead(db_with_migrations), executor)
    app.state.issues_repo = issues

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        responses = await asyncio.gather(
            *(
                client.post(f"/api/v1/issues/{issue_id}/inputs/{input_id}/answer", json={"answer": a}, headers=HEADERS)
                for a in ("wall", "monotonic")
            )
        )

    statuses = sorted(r.status_code for r in responses)
    resumes = executor.schedule_resume.call_count  # type: ignore[attr-defined]
    assert (statuses, resumes) == ([200, 409], 1), (
        f"two answers to an issue's question: {statuses}, the job resumed {resumes} times"
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("route", ["the job's", "the issue's"])
async def test_an_answer_during_a_drain_is_refused_and_taken_once_it_is_back(
    db_with_migrations: Any, route: str
) -> None:
    """An answer recorded during a drain could not resume the job, nor be sent again (it read as answered)."""
    issues, jobs, issue_id, job_id = await _waiting_issue(db_with_migrations)
    inputs = IssueInputsRepository(db_with_migrations)
    input_id = await inputs.create(issue_id, job_id, "Which clock?", asking_node="developer")
    executor = _issue_executor(issues, jobs)
    app = _app(jobs, inputs, executor)
    app.state.issues_repo = issues
    url = (
        f"/api/v1/jobs/{job_id}/inputs/{input_id}/answer"
        if route == "the job's"
        else f"/api/v1/issues/{issue_id}/inputs/{input_id}/answer"
    )

    async with AsyncClient(transport=ASGITransport(app=app, raise_app_exceptions=False), base_url="http://test") as c:
        executor._draining = True
        during = await c.post(url, json={"answer": "monotonic"}, headers=HEADERS)
        stored = (await inputs.get(input_id) or {}).get("status")
        executor._draining = False
        after = await c.post(url, json={"answer": "monotonic"}, headers=HEADERS)

    assert (during.status_code, stored) == (503, "pending"), (
        f"an answer during a drain on {route} route: {during.status_code}, the question reads {stored}"
    )
    assert after.status_code == 200, after.text
    assert executor.schedule_resume.call_count == 1  # type: ignore[attr-defined]


async def _answered_wait(db: Any, jobs: JobsRepository, issue_id: str | None = None) -> str:
    """A job left awaiting input with its one question answered: its resume was refused by a drain."""
    job_id = await jobs.create(repo="acme/app", task="Fix the idle timer", issue_id=issue_id)
    await jobs.update(job_id, status="running")
    await jobs.update(job_id, status="awaiting_input")
    inputs = IssueInputsRepository(db)
    input_id = await inputs.create(issue_id, job_id, "Which clock?", asking_node="developer")
    await inputs.answer(input_id, answer="monotonic", answered_by="ada")
    return job_id


@pytest.mark.asyncio
async def test_boot_resumes_the_jobs_waiting_for_answers_that_are_all_in(db_with_migrations: Any) -> None:
    issues, jobs, issue_id, _issue_job = await _waiting_issue(db_with_migrations)
    inputs = IssueInputsRepository(db_with_migrations)
    await inputs.create(issue_id, _issue_job, "Which clock?", asking_node="developer")
    await inputs.answer((await inputs.list_by_issue(issue_id))[0]["id"], answer="monotonic", answered_by="ada")
    alone = await _answered_wait(db_with_migrations, jobs)
    still_asking = await _waiting_job(jobs)
    await inputs.create(None, still_asking, "Which zone?", asking_node="developer")
    executor = _issue_executor(issues, jobs)
    executor._inputs = inputs
    resume_waits = getattr(executor, "resume_answered_waits", None)
    if resume_waits is None:
        pytest.fail("nothing resumes, at boot, a job whose answers are all in")

    resumed = await resume_waits()

    assert sorted(resumed) == sorted([alone, _issue_job]), f"boot resumed {resumed}"
    calls = {c.args[1]: c for c in executor.schedule_resume.call_args_list}  # type: ignore[attr-defined]
    assert "Q: Which clock?\nA: monotonic" in calls[alone].args[2], calls[alone]
    assert calls[_issue_job].kwargs.get("claimed_issue") == "awaiting_input", calls[_issue_job]
    assert (await issues.get(issue_id) or {})["status"] == "triaging", "the issue was not taken"
    assert (await jobs.get(still_asking) or {})["status"] == "awaiting_input", "a job still asking was resumed"


@pytest.mark.asyncio
async def test_a_job_its_own_route_took_while_the_issue_was_taken_starts_nothing_more(db_with_migrations: Any) -> None:
    """The issue's answer takes the issue; the job's answer then finds the issue taken, takes the job and resumes it;
    the issue's answer then finds no waiting job. The job is going on, so nothing is started for the issue."""
    issues, jobs, issue_id, job_id = await _waiting_issue(db_with_migrations)
    executor = _issue_executor(issues, jobs)
    claim = _claim_issue(issues)

    async def claim_answered(issue: str) -> bool:
        won = await claim(issue)
        # Between the issue's claim and its look for the waiting job: the job's own route answers.
        await executor.resume_answered(issue_id, job_id, "Q: q\nA: a", kind="resume_via_job_answer")
        return won

    issues.claim_answered = claim_answered  # type: ignore[method-assign]
    await executor.resume_or_execute(issue_id, "Q: q\nA: a")

    resumes = executor.schedule_resume.call_count  # type: ignore[attr-defined]
    fresh = executor.execute.await_count  # type: ignore[attr-defined]
    assert (resumes, fresh) == (1, 0), f"the job resumed {resumes} times and {fresh} fresh runs were started"
    assert (await issues.get(issue_id) or {})["status"] == "triaging", "the issue was put back while its job runs"


@pytest.mark.asyncio
async def test_a_jobs_own_answer_takes_its_waiting_issue_with_it(db_with_migrations: Any) -> None:
    issues, jobs, issue_id, job_id = await _waiting_issue(db_with_migrations)
    executor = _issue_executor(issues, jobs)

    assert await _resume_answered(executor)(issue_id, job_id, "Q: q\nA: a", kind="resume_via_job_answer") is True

    assert (await issues.get(issue_id) or {})["status"] == "triaging", "the job's answer left its issue to be taken"
    kwargs = executor.schedule_resume.call_args.kwargs  # type: ignore[attr-defined]
    assert kwargs.get("claimed_issue") == "awaiting_input", kwargs


@pytest.mark.asyncio
async def test_an_inbound_reply_does_nothing_with_an_issue_another_answer_took(db_with_migrations: Any) -> None:
    """The issue is taken and its job not yet: the one that took the issue resumes the job, not the reply."""
    issues, jobs, issue_id, job_id = await _waiting_issue(db_with_migrations)
    executor = _issue_executor(issues, jobs)
    assert await _claim_issue(issues)(issue_id) is True
    # Both read the issue as waiting just before the other took it.
    issues.get = AsyncMock(return_value={"id": issue_id, "status": "awaiting_input"})  # type: ignore[method-assign]

    await executor.resume_for_issue(issue_id)
    await executor.resume_or_execute(issue_id, "Q: q\nA: a")

    executor.schedule_resume.assert_not_called()  # type: ignore[attr-defined]
    assert (await jobs.get(job_id) or {})["status"] == "awaiting_input", "the job was taken from the one that took it"


@pytest.mark.asyncio
async def test_an_inbound_reply_does_not_resume_a_job_taken_since_it_looked(db_with_migrations: Any) -> None:
    """The reply found the job waiting and took the issue, but the job was taken in between: no second resume."""
    issues, jobs, issue_id, job_id = await _waiting_issue(db_with_migrations)
    executor = _issue_executor(issues, jobs)
    stale = [{"job_id": job_id, "issue_id": issue_id, "status": "awaiting_input"}]
    assert await _claim(jobs)(job_id) is True  # taken since the reply's lookup, which still reads it waiting
    jobs.list_all = AsyncMock(return_value=(stale, 1))  # type: ignore[method-assign]

    await executor.resume_for_issue(issue_id)

    executor.schedule_resume.assert_not_called()  # type: ignore[attr-defined]
