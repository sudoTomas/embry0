"""A change request that opened without its labels says so on the job (#472).

``ensure_pr_for_branch`` returned the pull request's URL whether or not its
labels went on; a failed label call only logged ``pr_label_failed``. So a
failed job's pull request could be opened without ``human-merge-only`` and
nothing on the job said so. The opener now reports the labels it could not
add (``labels_missing`` in its diagnostics, ``tests/unit/services/
test_github_prs.py``), and the job records that in its events, while it still
completes or fails with its ``pr_url``.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock

import pytest

from embry0.safety.error_codes import ErrorCode
from embry0.services.issue_executor import IssueExecutor
from tests.unit.services.test_failed_job_pr import BRANCH, PR, _executor, _failed, _patches, _terminal

SAID = "403 Resource not accessible by integration"


def _opener(*, noted: bool | None) -> AsyncMock:
    """``_open_change_request``: opens PR, and when ``noted`` is not None, reports its labels did not go on."""

    async def _open(self: Any, **kwargs: Any) -> str:
        diagnostics = kwargs.get("diagnostics")
        if noted is not None and diagnostics is not None:
            diagnostics.update(labels_missing=list(kwargs["labels"]), labels_error=SAID, labels_noted=noted)
        return PR

    return AsyncMock(side_effect=_open)


async def _outcome(result: dict[str, Any], opened: AsyncMock) -> IssueExecutor:
    ex = _executor()
    ex._broadcast_event = AsyncMock()  # type: ignore[method-assign]
    a, b, c, d = _patches(opened)
    with a, b, c, d:
        await ex._handle_workflow_result("iss-1", "job-1", result)
    return ex


def _label_messages(ex: IssueExecutor) -> list[str]:
    return [
        str(call.args[1].get("message"))
        for call in ex._broadcast_event.await_args_list  # type: ignore[attr-defined]
        if call.args[1].get("type") == "system" and "labels" in str(call.args[1].get("message"))
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize(("noted", "tail"), [(True, "A comment on it says so."), (False, "Add them on it by hand.")])
async def test_a_failed_jobs_pr_without_its_labels_is_recorded_on_the_job(noted: bool, tail: str) -> None:
    ex = await _outcome(_failed(ErrorCode.QA_FAILURES_UNRESOLVED.value), _opener(noted=noted))

    messages = _label_messages(ex)
    assert len(messages) == 1, f"the job does not record that its PR has no human-merge-only: {messages}"
    message = messages[0]
    assert PR in message and "embry0, human-merge-only" in message and f"{SAID}." in message, message
    assert "Only a person should merge it." in message, f"the job does not say why the label matters: {message}"
    assert message.endswith(tail), f"the job does not say whether the PR carries a note: {message}"
    terminal = _terminal(ex)
    assert (terminal["status"], terminal["pr_url"]) == ("failed", PR), (
        f"a PR without its labels is still the job's PR: {terminal}"
    )


@pytest.mark.asyncio
async def test_a_pr_whose_labels_went_on_records_nothing_about_them() -> None:
    result = {
        "triage_decision": {"action": "proceed"},
        "current_stage": "review_complete",
        "branch_name": BRANCH,
        "agent_outputs": [{"agent_type": "review", "is_error": False, "output": '{"decision": "approved"}'}],
    }
    ex = await _outcome(result, _opener(noted=None))

    assert _label_messages(ex) == []
    assert _terminal(ex)["pr_url"] == PR


@pytest.mark.asyncio
async def test_a_run_that_raised_records_its_prs_missing_labels_too() -> None:
    from embry0.orchestration.budget import BudgetOverrunError
    from tests.unit.services.test_failed_job_pr import _raising_stream

    ex = _executor()
    ex._broadcast_event = AsyncMock()  # type: ignore[method-assign]
    ex._jobs.get = AsyncMock(return_value={"trace_id": None, "repo": "acme/app", "task": "Fix the idle timer"})
    ex._execute_workflow_stream = _raising_stream(  # type: ignore[assignment]
        {"branch_name": BRANCH, "base_branch": "main", "current_stage": "developer_complete"},
        BudgetOverrunError(12.5, 10.0),
    )
    a, b, c, d = _patches(_opener(noted=True))
    with a, b, c, d:
        await ex._run_workflow("iss-1", "job-1", {"title": "Fix the idle timer", "body": "", "repo": "acme/app"})

    assert len(_label_messages(ex)) == 1, f"a raised run's PR without its labels is not recorded: {_label_messages(ex)}"
    assert _terminal(ex).get("pr_url") == PR
