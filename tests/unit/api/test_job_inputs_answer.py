"""POST /jobs/{job_id}/inputs/{input_id}/answer (EMB-43) — issue-less-job path."""

from unittest.mock import AsyncMock, MagicMock

import pytest

pytestmark = pytest.mark.asyncio


def _wire(app, *, inp, pending_after=0):
    inputs_repo = MagicMock()
    inputs_repo.get = AsyncMock(return_value=inp)
    inputs_repo.answer = AsyncMock()
    inputs_repo.count_pending_blocking_for_job = AsyncMock(return_value=pending_after)
    inputs_repo.list_all_answered_for_job = AsyncMock(
        return_value=[{"question": "Q1", "answer": "A1", "auto_answer": None}]
    )
    app.state.inputs_repo = inputs_repo
    executor = MagicMock()
    executor.resume = MagicMock(return_value=AsyncMock()())
    executor._track_task = MagicMock()
    app.state.issue_executor = executor
    return inputs_repo, executor


async def test_answer_resumes_issueless_job(api_client):
    from embry0.storage.repositories.jobs import JobsRepository

    jobs = JobsRepository(api_client.app.state.db)
    job_id = await jobs.create(repo="o/r", task="t")
    await api_client.app.state.db.execute("UPDATE jobs SET status='awaiting_input' WHERE job_id=$1", job_id)

    inp = {
        "id": "inp-1",
        "issue_id": None,
        "job_id": job_id,
        "status": "pending",
        "question": "Q1",
    }
    inputs_repo, executor = _wire(api_client.app, inp=inp, pending_after=0)

    r = await api_client.post(f"/api/v1/jobs/{job_id}/inputs/inp-1/answer", json={"answer": "A1"})
    assert r.status_code == 200
    inputs_repo.answer.assert_awaited_once()
    executor._track_task.assert_called_once()
    # resume called with issue_id=None for the issue-less job
    args, kwargs = executor.resume.call_args
    assert args[0] is None
    assert args[1] == job_id
    assert "Q: Q1" in args[2]


async def test_answer_no_resume_while_blocking_pending(api_client):
    from embry0.storage.repositories.jobs import JobsRepository

    jobs = JobsRepository(api_client.app.state.db)
    job_id = await jobs.create(repo="o/r", task="t")
    inp = {"id": "inp-2", "issue_id": None, "job_id": job_id, "status": "pending", "question": "Q"}
    _inputs_repo, executor = _wire(api_client.app, inp=inp, pending_after=1)

    r = await api_client.post(f"/api/v1/jobs/{job_id}/inputs/inp-2/answer", json={"answer": "A"})
    assert r.status_code == 200
    executor._track_task.assert_not_called()


async def test_answer_404_on_wrong_job(api_client):
    from embry0.storage.repositories.jobs import JobsRepository

    jobs = JobsRepository(api_client.app.state.db)
    job_id = await jobs.create(repo="o/r", task="t")
    inp = {"id": "inp-3", "issue_id": None, "job_id": "job-other", "status": "pending", "question": "Q"}
    _wire(api_client.app, inp=inp)
    r = await api_client.post(f"/api/v1/jobs/{job_id}/inputs/inp-3/answer", json={"answer": "A"})
    assert r.status_code == 404


async def test_answer_409_on_already_answered(api_client):
    from embry0.storage.repositories.jobs import JobsRepository

    jobs = JobsRepository(api_client.app.state.db)
    job_id = await jobs.create(repo="o/r", task="t")
    inp = {"id": "inp-4", "issue_id": None, "job_id": job_id, "status": "answered", "question": "Q"}
    _wire(api_client.app, inp=inp)
    r = await api_client.post(f"/api/v1/jobs/{job_id}/inputs/inp-4/answer", json={"answer": "A"})
    assert r.status_code == 409
