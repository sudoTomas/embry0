"""Unit tests for IssueExecutor event broadcasting and cost accumulation."""

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest


@pytest.mark.asyncio
async def test_broadcast_event_accumulates_cost_across_events():
    """Successive cost_update events must ADD, not overwrite."""
    from legion.services.issue_executor import IssueExecutor

    jobs = MagicMock()
    jobs.append_log_event = AsyncMock()
    jobs.increment_cost = AsyncMock()
    jobs.update = AsyncMock()

    executor = IssueExecutor.__new__(IssueExecutor)
    executor._jobs = jobs
    from legion.api.events.bus import EventBus

    executor._event_bus = EventBus()

    await executor._broadcast_event("job-1", {"type": "cost_update", "cost_usd": 0.10})
    await executor._broadcast_event("job-1", {"type": "cost_update", "cost_usd": 0.05})
    await executor._broadcast_event("job-1", {"type": "cost_update", "cost_usd": 0.20})

    assert jobs.increment_cost.await_count == 3
    calls = [call.args for call in jobs.increment_cost.await_args_list]
    assert calls == [("job-1", 0.10), ("job-1", 0.05), ("job-1", 0.20)]

    update_calls_for_cost = [c for c in jobs.update.await_args_list if "total_cost_usd" in c.kwargs]
    assert update_calls_for_cost == [], f"update() must not be used for cost accumulation: {update_calls_for_cost}"


@pytest.mark.asyncio
async def test_broadcast_event_ignores_cost_update_without_amount():
    """cost_update events with missing or zero cost_usd must not call increment_cost."""
    from legion.services.issue_executor import IssueExecutor

    jobs = MagicMock()
    jobs.append_log_event = AsyncMock()
    jobs.increment_cost = AsyncMock()

    executor = IssueExecutor.__new__(IssueExecutor)
    executor._jobs = jobs
    from legion.api.events.bus import EventBus

    executor._event_bus = EventBus()

    await executor._broadcast_event("job-1", {"type": "cost_update"})
    await executor._broadcast_event("job-1", {"type": "cost_update", "cost_usd": 0})
    await executor._broadcast_event("job-1", {"type": "something_else", "cost_usd": 0.99})

    assert jobs.increment_cost.await_count == 0


@pytest.mark.asyncio
async def test_broadcast_event_stamps_event_seq_from_persist():
    """The seq returned by append_log_event must be stamped onto the event
    before it reaches subscribers, so WS clients can use it as a cursor."""
    from legion.api.events.bus import EventBus
    from legion.services.issue_executor import IssueExecutor

    jobs = MagicMock()
    jobs.append_log_event = AsyncMock(return_value=12345)
    jobs.increment_cost = AsyncMock()

    bus = EventBus()
    queue = await bus.subscribe("job-1")

    executor = IssueExecutor.__new__(IssueExecutor)
    executor._jobs = jobs
    executor._event_bus = bus

    await executor._broadcast_event("job-1", {"type": "progress", "message": "hi"})

    delivered = queue.get_nowait()
    assert delivered["event_seq"] == 12345
    assert delivered["type"] == "progress"


@pytest.mark.asyncio
async def test_broadcast_event_no_event_seq_when_persist_fails():
    """If append_log_event raises, the event still reaches subscribers but
    carries no event_seq stamp (nothing to stamp since nothing was persisted)."""
    from legion.api.events.bus import EventBus
    from legion.services.issue_executor import IssueExecutor

    jobs = MagicMock()
    jobs.append_log_event = AsyncMock(side_effect=RuntimeError("db down"))
    jobs.increment_cost = AsyncMock()

    bus = EventBus()
    queue = await bus.subscribe("job-1")

    executor = IssueExecutor.__new__(IssueExecutor)
    executor._jobs = jobs
    executor._event_bus = bus

    await executor._broadcast_event("job-1", {"type": "progress", "message": "hi"})

    delivered = queue.get_nowait()
    assert "event_seq" not in delivered
    assert delivered["type"] == "progress"


@pytest.mark.asyncio
async def test_track_task_registers_and_removes_on_completion():
    """_track_task adds to the set; callback removes on normal completion."""
    from legion.api.events.bus import EventBus
    from legion.services.issue_executor import IssueExecutor

    executor = IssueExecutor.__new__(IssueExecutor)
    executor._background_tasks = set()
    executor._event_bus = EventBus()

    completed = asyncio.Event()

    async def _work() -> None:
        completed.set()

    task = executor._track_task(_work(), kind="test", job_id="job-1")
    assert task in executor._background_tasks
    await completed.wait()
    await asyncio.sleep(0)  # let done-callback fire
    assert task not in executor._background_tasks


@pytest.mark.asyncio
async def test_track_task_logs_and_publishes_on_failure(capsys):
    """If the coroutine raises an unexpected exception, _track_task logs it
    AND publishes a job_failed event via the bus (so WS clients see it)."""
    from legion.api.events.bus import EventBus
    from legion.services.issue_executor import IssueExecutor

    executor = IssueExecutor.__new__(IssueExecutor)
    executor._background_tasks = set()
    executor._event_bus = EventBus()

    queue = await executor._event_bus.subscribe("job-1")

    async def _boom() -> None:
        raise RuntimeError("kaboom")

    task = executor._track_task(_boom(), kind="workflow", job_id="job-1")
    # Awaiting the failed task always re-raises the exception for the caller.
    # What _track_task guarantees is that the done-callback ALSO sees the
    # exception (via .exception()) and logs/publishes it — even if no one awaits.
    try:
        await task
    except RuntimeError:
        pass
    await asyncio.sleep(0)

    # Failure was logged
    captured = capsys.readouterr()
    assert "background_task_failed" in captured.out
    assert "kaboom" in captured.out

    # job_failed event was published
    assert queue.qsize() == 1
    evt = queue.get_nowait()
    assert evt["type"] == "job_failed"
    assert evt["job_id"] == "job-1"
    assert "kaboom" in evt.get("error", "")


@pytest.mark.asyncio
async def test_track_task_ignores_cancelled_error(capsys):
    """Task cancellation during shutdown is normal; must NOT log as an error
    or emit a job_failed event."""
    from legion.api.events.bus import EventBus
    from legion.services.issue_executor import IssueExecutor

    executor = IssueExecutor.__new__(IssueExecutor)
    executor._background_tasks = set()
    executor._event_bus = EventBus()

    queue = await executor._event_bus.subscribe("job-1")

    async def _slow() -> None:
        await asyncio.sleep(10)

    task = executor._track_task(_slow(), kind="workflow", job_id="job-1")
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass
    await asyncio.sleep(0)

    captured = capsys.readouterr()
    assert "background_task_failed" not in captured.out
    assert queue.qsize() == 0  # no job_failed event on cancellation


@pytest.mark.asyncio
async def test_track_task_removes_from_set_on_exception():
    """Even when the coro raises, the task must be removed from the tracking set."""
    from legion.api.events.bus import EventBus
    from legion.services.issue_executor import IssueExecutor

    executor = IssueExecutor.__new__(IssueExecutor)
    executor._background_tasks = set()
    executor._event_bus = EventBus()

    async def _boom() -> None:
        raise RuntimeError("x")

    task = executor._track_task(_boom(), kind="workflow", job_id="job-1")
    try:
        await task
    except RuntimeError:
        pass
    await asyncio.sleep(0)
    assert task not in executor._background_tasks


@pytest.mark.asyncio
async def test_handle_needs_info_rolls_back_on_update_failure():
    """If updating job status fails mid-commit, input rows must NOT persist."""
    from legion.services.issue_executor import IssueExecutor

    # Build a fake db with a transaction() that raises on the second statement
    class _FakeConn:
        def __init__(self) -> None:
            self.executed: list[str] = []
            self._fail_on = "UPDATE jobs"

        async def fetchval(self, query: str, *args):
            return None

        async def execute(self, query: str, *args):
            if self._fail_on in query:
                raise RuntimeError("simulated db failure")
            self.executed.append(query)

    class _FakeDb:
        def __init__(self) -> None:
            self.conn = _FakeConn()

        def transaction(self):
            fake_conn = self.conn

            class _TxnCM:
                async def __aenter__(self_inner):
                    return fake_conn

                async def __aexit__(self_inner, exc_type, exc, tb):
                    return False  # propagate

            return _TxnCM()

    db = _FakeDb()
    executor = IssueExecutor.__new__(IssueExecutor)
    executor._db = db
    executor._inputs = MagicMock()  # should NOT be called since we're inside txn
    executor._jobs = MagicMock()
    executor._issues = MagicMock()
    executor._config = None
    executor._audit_log_path = None

    with pytest.raises(RuntimeError, match="simulated db failure"):
        await executor._handle_needs_info(
            issue_id="iss-1",
            job_id="job-1",
            decision={
                "questions": [
                    {"question": "Q1?", "importance": "blocking"},
                    {"question": "Q2?", "importance": "blocking"},
                ],
                "asking_node": "triage",
            },
        )

    # Verify: the UPDATE jobs failed, so NO inserts should have been kept
    # (the fake exec tracked them, but in a real DB transaction they rolled back).
    # This test verifies the sequence by inspecting what was attempted.
    # The test of _actual_ rollback behavior is in the storage layer tests.
    insert_count = sum(1 for q in db.conn.executed if "INSERT INTO issue_inputs" in q)
    assert insert_count == 2, "both INSERTs should have been attempted before the UPDATE"
