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


@pytest.mark.asyncio
async def test_a_wait_another_answer_took_is_not_resumed_or_started_again(db_with_migrations: Any) -> None:
    jobs = JobsRepository(db_with_migrations)
    job_id = await _waiting_job(jobs)
    executor = _executor(jobs)
    executor.waiting_job = AsyncMock(return_value={"job_id": job_id, "status": "awaiting_input"})  # type: ignore[method-assign]
    executor.execute = AsyncMock()  # type: ignore[method-assign]
    executor._issues = MagicMock(update=AsyncMock(), get=AsyncMock(return_value={"status": "awaiting_input"}))
    executor._jobs = jobs
    assert await _claim(jobs)(job_id) is True  # another answer took it first

    await executor.resume_or_execute("iss-1", "Q: which clock?\nA: monotonic")
    executor._jobs.list_all = AsyncMock(  # type: ignore[method-assign]
        return_value=([{"job_id": job_id, "status": "awaiting_input"}], 1)
    )
    await executor.resume_for_issue("iss-1")

    executor.schedule_resume.assert_not_called()  # type: ignore[attr-defined]
    executor.execute.assert_not_awaited()
    executor._issues.update.assert_not_awaited()


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
