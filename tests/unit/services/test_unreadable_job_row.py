"""A job whose row holds a value too deep to read does not run on defaults (#527).

The codec raises ``StoredValueTooDeep`` for such a row. A run read its job row
inside a catch-all and went on without it, so its config (a budget cap, models,
narrowed tools) would have been the defaults. It now fails, saying why, before
anything starts; ``done_when.current`` raises the error instead of keeping its
fallback.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock

import pytest

from embry0.storage.database import StoredValueTooDeep
from tests.unit.services.test_failed_job_pr import PR, _executor, _patches


def _stream_never_started() -> tuple[Any, list[str]]:
    started: list[str] = []

    async def _stream(input_value, issue_id, job_id, state_sink=None, **kw):  # noqa: ANN001, ANN003, ARG001
        started.append(job_id)
        raise RuntimeError("the workflow ran")

    return _stream, started


def _failure(ex: Any) -> str:
    updates = [c.kwargs for c in ex._jobs.update.await_args_list if c.kwargs.get("status")]
    assert updates, "the job was never given a status"
    assert updates[-1]["status"] == "failed", f"the job ran without its row: {updates[-1]}"
    return str(updates[-1].get("error_message") or updates[-1])


def _unreadable_executor() -> Any:
    ex = _executor()
    ex._broadcast_event = AsyncMock()  # type: ignore[method-assign]
    ex._jobs.get = AsyncMock(side_effect=StoredValueTooDeep("jobs"))
    return ex


@pytest.mark.asyncio
async def test_a_run_whose_job_row_is_too_deep_to_read_fails_before_it_starts() -> None:
    ex = _unreadable_executor()
    stream, started = _stream_never_started()
    ex._execute_workflow_stream = stream  # type: ignore[assignment]
    a, b, c, d = _patches(AsyncMock(return_value=PR))
    with a, b, c, d:
        await ex._run_workflow("iss-1", "job-1", {"title": "t", "body": "", "repo": "acme/app"})
    assert started == [], "the workflow started without the job's row"
    assert "too deep for json.loads" in _failure(ex)


@pytest.mark.asyncio
async def test_a_review_whose_job_row_is_too_deep_to_read_fails_before_it_starts() -> None:
    from embry0.services._executor_run import _run_pr_review_workflow

    ex = _unreadable_executor()
    ex._refuse_if_over_budget = AsyncMock(return_value=False)  # type: ignore[method-assign]
    stream, started = _stream_never_started()
    ex._execute_workflow_stream = stream  # type: ignore[assignment]
    a, b, c, d = _patches(AsyncMock(return_value=PR))
    with a, b, c, d:
        await _run_pr_review_workflow(ex, "job-1")
    assert started == [], "the review started without the job's row"
    ex._refuse_if_over_budget.assert_not_awaited()
    assert "too deep for json.loads" in _failure(ex)


@pytest.mark.asyncio
async def test_done_when_raises_rather_than_keeping_its_fallback() -> None:
    from embry0.services.done_when import as_list, current

    fallback = {"source": "triage", "items": [{"id": "c1", "done": "the build passes"}]}
    assert as_list(fallback) is not None, "the fallback is not a list done_when reads"
    jobs = AsyncMock()
    jobs.get = AsyncMock(side_effect=StoredValueTooDeep("jobs"))
    with pytest.raises(StoredValueTooDeep):
        await current(jobs, "job-1", fallback)
    jobs.get = AsyncMock(side_effect=ConnectionError("blip"))
    assert await current(jobs, "job-1", fallback) == as_list(fallback), (
        "any other failed read keeps the fallback, as before"
    )


@pytest.mark.asyncio
async def test_a_node_that_meets_a_value_too_deep_to_read_fails_the_job_with_the_reason() -> None:
    """A node's config read raises it (an agent's definition, the model policy, the budget, the preferences): the
    job fails, as a storage failure, saying why."""
    ex = _executor()
    ex._broadcast_event = AsyncMock()  # type: ignore[method-assign]
    ex._jobs.get = AsyncMock(return_value={"trace_id": None, "repo": "acme/app", "task": "t"})

    async def _stream(input_value, issue_id, job_id, state_sink=None, **kw):  # noqa: ANN001, ANN003, ARG001
        raise StoredValueTooDeep("agent_definitions")

    ex._execute_workflow_stream = _stream  # type: ignore[assignment]
    a, b, c, d = _patches(AsyncMock(return_value=PR))
    with a, b, c, d:
        await ex._run_workflow("iss-1", "job-1", {"title": "t", "body": "", "repo": "acme/app"})
    updates = [call.kwargs for call in ex._jobs.update.await_args_list if call.kwargs.get("status")]
    assert updates[-1]["status"] == "failed", updates[-1]
    assert updates[-1].get("error_code") == "ERR_STORAGE", f"not a storage failure: {updates[-1]}"
    assert "in agent_definitions is nested too deep" in str(updates[-1].get("error_message")), updates[-1]
