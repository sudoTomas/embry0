"""Every terminal status write the executor service performs is followed by job_finished (spec 3.5)."""

from __future__ import annotations

import ast
import inspect
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import embry0.services._executor_outcomes as _executor_outcomes
import embry0.services._executor_run as _executor_run
import embry0.services.issue_executor as issue_executor
from embry0.services.issue_executor import IssueExecutor
from embry0.storage.schemas import JobStatus

_TERMINAL = {"completed", "failed", "cancelled", "partial", "expired", "skipped"}


def _order_recording_executor():
    order: list[tuple] = []
    ex = IssueExecutor.__new__(IssueExecutor)

    async def _update(job_id, **kw):
        order.append(("update", kw.get("status")))

    async def _finish(job_id, *, error_code):
        order.append(("finish", error_code))

    async def _announce(job_id):
        order.append(("announce", job_id))

    ex._jobs = MagicMock(update=AsyncMock(side_effect=_update), get=AsyncMock(return_value=None))
    ex._finish_credential_uses = AsyncMock(side_effect=_finish)
    ex._announce_finished = AsyncMock(side_effect=_announce)
    return ex, order


async def test_split_completed_announces_after_the_stamp():
    ex, order = _order_recording_executor()
    ex._issues = MagicMock(
        get=AsyncMock(return_value={"priority": "low", "labels": [], "repo": "o/r"}),
        create=AsyncMock(return_value="child-1"),
        update=AsyncMock(),
        update_parent_status=AsyncMock(),
    )
    ex._emit_audit = AsyncMock()
    ex._db = None
    ex._audit_log_path = None
    await ex._handle_split("iss-1", "job-1", {"sub_tasks": [{"task": "a"}]})
    assert order == [("update", "completed"), ("finish", None), ("announce", "job-1")], order


async def test_needs_info_zero_questions_announces_the_failure():
    ex, order = _order_recording_executor()
    ex._issues = MagicMock(get=AsyncMock(return_value={"id": "iss-1"}), update=AsyncMock())
    await ex._handle_needs_info("iss-1", "job-1", {"questions": [], "asking_node": "triage"})
    assert order == [("update", "failed"), ("finish", "ERR_EMPTY_INTERRUPT"), ("announce", "job-1")], order


async def test_cancel_job_announces_the_cancellation(monkeypatch):
    ex, order = _order_recording_executor()
    ex._background_tasks = set()
    ex._tasks_by_job = {}
    ex._event_bus = None
    ex._database_url = "postgres://stub"
    ex._audit_log_path = None
    ex._db = MagicMock()
    ex._issues = MagicMock(update=AsyncMock())
    ex._sandbox = None
    ex._cleanup_job_volumes = AsyncMock()
    import embry0.orchestration.checkpoint as ckpt_mod

    monkeypatch.setattr(ckpt_mod, "purge_thread", AsyncMock())
    with patch.object(issue_executor, "emit_audit", new=AsyncMock()):
        await ex.cancel_job("job-1")
    assert order == [("update", "cancelled"), ("finish", "cancelled"), ("announce", "job-1")], order


# --- structural guard: no terminal write escapes the announcement ---------------


def _is_terminal_job_update(node: ast.AST) -> bool:
    """`await self._jobs.update(..., status=<terminal literal | terminal JobStatus member | variable>)`."""
    if not (isinstance(node, ast.Expr) and isinstance(node.value, ast.Await)):
        return False
    call = node.value.value
    if not (isinstance(call, ast.Call) and ast.unparse(call.func) == "self._jobs.update"):
        return False
    for kw in call.keywords:
        if kw.arg == "status":
            if isinstance(kw.value, ast.Constant):
                return kw.value.value in _TERMINAL
            if isinstance(kw.value, ast.Attribute):
                member = JobStatus.__members__.get(kw.value.attr)
                return member is not None and member.value in _TERMINAL
            return isinstance(kw.value, ast.Name)
    return False


def _is_bulk_fail(node: ast.AST) -> bool:
    """`... = await self._jobs.fail_jobs(...)` or `await self._jobs.fail_jobs(...)`."""
    value = node.value if isinstance(node, (ast.Expr, ast.Assign)) else None
    call = value.value if isinstance(value, ast.Await) else None
    return isinstance(call, ast.Call) and ast.unparse(call.func) == "self._jobs.fail_jobs"


def _announces(block: list[ast.stmt]) -> bool:
    return any("self._announce_finished(" in ast.unparse(stmt) for stmt in block)


@pytest.mark.parametrize(
    ("source", "terminal"),
    [
        ('await self._jobs.update(job_id, status="failed")', True),
        ('await self._jobs.update(job_id, status="running")', False),
        ("await self._jobs.update(job_id, status=final_status)", True),
        ("await self._jobs.update(job_id, status=JobStatus.FAILED)", True),
        ("await self._jobs.update(job_id, status=JobStatus.SKIPPED)", True),
        ("await self._jobs.update(job_id, status=JobStatus.RUNNING)", False),
        ("await self._jobs.update(job_id, status=JobStatus.AWAITING_INPUT)", False),
        ("await self._jobs.update(job_id, pr_url=url)", False),
    ],
)
def test_the_matcher_sees_every_spelling_of_a_terminal_write(source: str, terminal: bool) -> None:
    """A spelling the matcher misses is a terminal write the guard below never checks."""
    stmt = ast.parse(f"async def f():\n    {source}\n").body[0].body[0]  # type: ignore[attr-defined]
    assert _is_terminal_job_update(stmt) is terminal, f"matcher got {source!r} wrong"


def test_every_terminal_status_write_is_followed_by_job_finished():
    missing = []
    found = 0
    for module in (_executor_outcomes, _executor_run, issue_executor):
        tree = ast.parse(inspect.getsource(module))
        for parent in ast.walk(tree):
            for field in ("body", "orelse", "finalbody"):
                block = getattr(parent, field, None)
                if not isinstance(block, list):
                    continue
                for i, stmt in enumerate(block):
                    if _is_terminal_job_update(stmt) or _is_bulk_fail(stmt):
                        found += 1
                        if not _announces(block[i + 1 :]):
                            missing.append(f"{module.__name__}:{stmt.lineno}")
    assert found >= 16, f"guard matched only {found} terminal writes; the matcher is broken"
    assert not missing, f"terminal job status writes not followed by _announce_finished: {missing}"
