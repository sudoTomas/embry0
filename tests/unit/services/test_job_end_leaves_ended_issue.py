"""A job that ends never reopens an issue closed or cancelled while it ran (#521).

When a job ended, several of the executor's paths wrote its issue back to
``open`` without reading it, and ``closed -> open`` is an allowed transition
(it is how a reopen is recorded): an issue a person closed while its job ran
was reopened by embry0 when the job ended. #518 gave ``_fail_resume`` and
``cancel_job`` ``IssueExecutor._issue_back_to_open``, which leaves a closed or
cancelled issue as it is; every other job-end path now goes through it too,
and a guard keeps a bare ``open`` write from coming back.
"""

from __future__ import annotations

import ast
import inspect
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import embry0.services._executor_outcomes as _executor_outcomes
import embry0.services._executor_run as _executor_run
import embry0.services.issue_executor as issue_executor
from embry0.services.issue_executor import IssueExecutor


def _opens(node: ast.AST) -> bool:
    """`await self._issues.update(<issue>, status="open")`."""
    if not (isinstance(node, ast.Await) and isinstance(node.value, ast.Call)):
        return False
    call = node.value
    return ast.unparse(call.func) == "self._issues.update" and any(
        kw.arg == "status" and isinstance(kw.value, ast.Constant) and kw.value.value == "open" for kw in call.keywords
    )


def test_no_job_end_writes_open_but_through_the_one_rule():
    """Only `_issue_back_to_open` writes an issue to open; everything else asks it."""
    found: list[str] = []
    for module in (_executor_run, _executor_outcomes, issue_executor):
        tree = ast.parse(inspect.getsource(module))
        for fn in ast.walk(tree):
            if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)) or fn.name == "_issue_back_to_open":
                continue
            found += [f"{module.__name__}:{fn.name}:{node.lineno}" for node in ast.walk(fn) if _opens(node)]
    assert found == [], f"job ends that reopen an issue without asking whether it ended: {found}"


def test_the_guard_sees_a_bare_open_write():
    stmt = ast.parse('async def f():\n    await self._issues.update(issue_id, status="open")\n').body[0]
    assert any(_opens(node) for node in ast.walk(stmt)), "the guard measures nothing"


def _executor(issue_status: str) -> IssueExecutor:
    ex = IssueExecutor.__new__(IssueExecutor)
    ex._jobs = MagicMock(update=AsyncMock(), get=AsyncMock(return_value=None))
    ex._issues = MagicMock(
        get=AsyncMock(
            return_value={"id": "iss-1", "status": issue_status, "priority": "low", "labels": [], "repo": "o/r"}
        ),
        create=AsyncMock(return_value="child-1"),
        update=AsyncMock(),
        update_parent_status=AsyncMock(),
    )
    ex._finish_credential_uses = AsyncMock()  # type: ignore[method-assign]
    ex._announce_finished = AsyncMock()  # type: ignore[method-assign]
    ex._emit_audit = AsyncMock()  # type: ignore[method-assign]
    ex._db = None
    ex._audit_log_path = None
    return ex


def _opened(ex: IssueExecutor) -> list[Any]:
    return [c for c in ex._issues.update.await_args_list if c.kwargs.get("status") == "open"]  # type: ignore[attr-defined]


async def _split_without_tasks(ex: IssueExecutor) -> None:
    await ex._handle_split("iss-1", "job-1", {"sub_tasks": []})


async def _split_into_children(ex: IssueExecutor) -> None:
    await ex._handle_split("iss-1", "job-1", {"sub_tasks": [{"task": "a"}]})


async def _needs_info_without_questions(ex: IssueExecutor) -> None:
    await ex._handle_needs_info("iss-1", "job-1", {"questions": [], "asking_node": "triage"})


async def _paused_at_max_retries(ex: IssueExecutor) -> None:
    with patch.object(_executor_run, "_announce_paused", new=AsyncMock()):
        await ex._handle_interrupt("iss-1", "job-1", {"reason": "max_retries"})


async def _resume_or_execute_whose_run_fails(ex: IssueExecutor) -> None:
    ex._issues.claim_answered = AsyncMock(return_value=True)  # type: ignore[attr-defined]
    ex._jobs.list_all = AsyncMock(return_value=([], 0))
    ex.execute = AsyncMock(side_effect=RuntimeError("boom"))  # type: ignore[method-assign]
    await ex.resume_or_execute("iss-1", "Q: q\nA: a")


ENDINGS = {
    "a triage split with no tasks": _split_without_tasks,
    "a triage split into children": _split_into_children,
    "a question with no questions": _needs_info_without_questions,
    "a pause at max retries": _paused_at_max_retries,
    "a fresh run after the last answer that fails to start": _resume_or_execute_whose_run_fails,
}


@pytest.mark.asyncio
@pytest.mark.parametrize("ending", list(ENDINGS))
@pytest.mark.parametrize(("issue_status", "reopened"), [("closed", False), ("cancelled", False), ("in_progress", True)])
async def test_a_job_end_reopens_a_live_issue_but_never_an_ended_one(
    ending: str, issue_status: str, reopened: bool
) -> None:
    ex = _executor(issue_status)
    await ENDINGS[ending](ex)
    assert bool(_opened(ex)) is reopened, f"{ending}, its issue {issue_status}: open written {_opened(ex)}"


@pytest.mark.asyncio
@pytest.mark.parametrize(("issue_status", "reopened"), [("closed", False), ("in_progress", True)])
async def test_a_run_that_fails_never_reopens_an_ended_issue(issue_status: str, reopened: bool) -> None:
    """The run of an issue whose job failed (the most common end): `_run_workflow`'s failure path."""
    from tests.unit.services import test_failed_job_pr

    with patch.object(test_failed_job_pr, "_executor", new=_failed_job_executor(issue_status)):
        ex, _opened_pr = await test_failed_job_pr._raised(
            {"branch_name": "embry0/iss-1-fix", "current_stage": "developer_complete"}
        )

    opened = [c for c in ex._issues.update.await_args_list if c.kwargs.get("status") == "open"]  # type: ignore[attr-defined]
    assert bool(opened) is reopened, f"a failed run, its issue {issue_status}: open written {opened}"


def _failed_job_executor(issue_status: str) -> Any:
    from tests.unit.services import test_failed_job_pr

    build = test_failed_job_pr._executor

    def _with_issue() -> IssueExecutor:
        ex = build()
        ex._issues.get = AsyncMock(return_value={"id": "iss-1", "status": issue_status})
        return ex

    return _with_issue
