"""A job whose stored ``pipeline_config`` was read only in part does not run on what is left of it (#527).

Read in part, the config has lost every key past the cut: its budget, its
models, its narrowed tools. Run on the rest, each would be its default. The run
refuses it before anything starts, and the job fails saying why.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock

import pytest

from embry0.safety.part_read import TOO_LONG_TO_READ, PartReadDict
from tests.unit.services.test_failed_job_pr import PR, _executor, _patches


def _cut_config() -> dict[str, Any]:
    """A config read in part, then copied (a copy of the outermost object is no longer marked; what it holds is)."""
    return dict(PartReadDict({"a": PartReadDict({"p": "x", "q": TOO_LONG_TO_READ})}))


def _stream_never_started() -> tuple[Any, list[str]]:
    started: list[str] = []

    async def _stream(input_value, issue_id, job_id, state_sink=None, **kw):  # noqa: ANN001, ANN003, ARG001
        started.append(job_id)
        if False:
            yield None

    return _stream, started


def _failure(ex: Any) -> str:
    updates = [c.kwargs for c in ex._jobs.update.await_args_list if c.kwargs.get("status")]
    assert updates, "the job was never given a status"
    assert updates[-1]["status"] == "failed", f"the job ran on what was left of its config: {updates[-1]}"
    return str(updates[-1].get("error_message") or updates[-1].get("error") or updates[-1])


@pytest.mark.asyncio
async def test_a_run_refuses_a_config_read_in_part() -> None:
    ex = _executor()
    ex._broadcast_event = AsyncMock()  # type: ignore[method-assign]
    ex._jobs.get = AsyncMock(
        return_value={"trace_id": None, "repo": "acme/app", "task": "t", "pipeline_config": _cut_config()}
    )
    stream, started = _stream_never_started()
    ex._execute_workflow_stream = stream  # type: ignore[assignment]
    a, b, c, d = _patches(AsyncMock(return_value=PR))
    with a, b, c, d:
        await ex._run_workflow("iss-1", "job-1", {"title": "t", "body": "", "repo": "acme/app"})
    assert started == [], "the workflow started on a config read in part"
    assert "read only in part" in _failure(ex)


@pytest.mark.asyncio
async def test_a_review_refuses_a_config_read_in_part() -> None:
    from embry0.services._executor_run import _run_pr_review_workflow

    ex = _executor()
    ex._broadcast_event = AsyncMock()  # type: ignore[method-assign]
    ex._refuse_if_over_budget = AsyncMock(return_value=False)  # type: ignore[method-assign]
    ex._jobs.get = AsyncMock(
        return_value={"trace_id": None, "repo": "acme/app", "task": "t", "pipeline_config": _cut_config()}
    )
    stream, started = _stream_never_started()
    ex._execute_workflow_stream = stream  # type: ignore[assignment]
    a, b, c, d = _patches(AsyncMock(return_value=PR))
    with a, b, c, d:
        await _run_pr_review_workflow(ex, "job-1")
    assert started == [], "the review started on a config read in part"
    ex._refuse_if_over_budget.assert_not_awaited()
    assert "read only in part" in _failure(ex)
