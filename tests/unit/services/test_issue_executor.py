"""Unit tests for IssueExecutor event broadcasting and cost accumulation."""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


@pytest.mark.asyncio
async def test_broadcast_event_accumulates_cost_across_events():
    """Successive cost_update events must ADD, not overwrite."""
    from embry0.services.issue_executor import IssueExecutor

    jobs = MagicMock()
    jobs.append_log_event = AsyncMock()
    jobs.increment_cost = AsyncMock()
    jobs.update = AsyncMock()

    executor = IssueExecutor.__new__(IssueExecutor)
    executor._jobs = jobs
    from embry0.api.events.bus import EventBus

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
    from embry0.services.issue_executor import IssueExecutor

    jobs = MagicMock()
    jobs.append_log_event = AsyncMock()
    jobs.increment_cost = AsyncMock()

    executor = IssueExecutor.__new__(IssueExecutor)
    executor._jobs = jobs
    from embry0.api.events.bus import EventBus

    executor._event_bus = EventBus()

    await executor._broadcast_event("job-1", {"type": "cost_update"})
    await executor._broadcast_event("job-1", {"type": "cost_update", "cost_usd": 0})
    await executor._broadcast_event("job-1", {"type": "something_else", "cost_usd": 0.99})

    assert jobs.increment_cost.await_count == 0


@pytest.mark.asyncio
async def test_broadcast_event_stamps_event_seq_from_persist():
    """The seq returned by append_log_event must be stamped onto the event
    before it reaches subscribers, so WS clients can use it as a cursor."""
    from embry0.api.events.bus import EventBus
    from embry0.services.issue_executor import IssueExecutor

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
    from embry0.api.events.bus import EventBus
    from embry0.services.issue_executor import IssueExecutor

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
    from embry0.api.events.bus import EventBus
    from embry0.services.issue_executor import IssueExecutor

    executor = IssueExecutor.__new__(IssueExecutor)
    executor._background_tasks = set()
    executor._tasks_by_job = {}
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
    from embry0.api.events.bus import EventBus
    from embry0.services.issue_executor import IssueExecutor

    executor = IssueExecutor.__new__(IssueExecutor)
    executor._background_tasks = set()
    executor._tasks_by_job = {}
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
    assert "kaboom" in evt.get("message", "")


@pytest.mark.asyncio
async def test_track_task_ignores_cancelled_error(capsys):
    """Task cancellation during shutdown is normal; must NOT log as an error
    or emit a job_failed event."""
    from embry0.api.events.bus import EventBus
    from embry0.services.issue_executor import IssueExecutor

    executor = IssueExecutor.__new__(IssueExecutor)
    executor._background_tasks = set()
    executor._tasks_by_job = {}
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
    from embry0.api.events.bus import EventBus
    from embry0.services.issue_executor import IssueExecutor

    executor = IssueExecutor.__new__(IssueExecutor)
    executor._background_tasks = set()
    executor._tasks_by_job = {}
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
async def test_handle_needs_info_sequences_inserts_before_updates():
    """Sequencing guard: _handle_needs_info must issue ALL INSERTs via the
    transaction conn BEFORE the status CAS, and a non-conflict CAS failure
    must propagate up to the caller (only StatusTransitionConflict is
    swallowed as a benign race — RAV-1063).

    This test verifies the SEQUENCE of SQL dispatch (so a future refactor
    can't silently move an INSERT onto a pool-level connection outside the
    transaction). It does NOT verify transactional rollback semantics — the
    real-DB end-to-end behaviour is covered in the storage layer tests.
    """
    from embry0.services.issue_executor import IssueExecutor

    class _FakeConn:
        def __init__(self) -> None:
            self.executed: list[str] = []

        async def fetchval(self, query: str, *args):
            return None

        async def execute(self, query: str, *args):
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
    executor._jobs = MagicMock(cas_transition_on_conn=AsyncMock(side_effect=RuntimeError("simulated db failure")))
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

    # Verify: the status CAS failed, so NO inserts should have been kept
    # (the fake exec tracked them, but in a real DB transaction they rolled back).
    # This test verifies the sequence by inspecting what was attempted.
    insert_count = sum(1 for q in db.conn.executed if "INSERT INTO issue_inputs" in q)
    assert insert_count == 2, "both INSERTs should have been attempted before the status CAS"
    # The CAS ran on the transaction's connection, after the inserts.
    executor._jobs.cas_transition_on_conn.assert_awaited_once()
    assert executor._jobs.cas_transition_on_conn.await_args.args[0] is db.conn


@pytest.mark.asyncio
async def test_track_task_registers_in_tasks_by_job():
    """_track_task must populate _tasks_by_job with the new task."""
    from embry0.api.events.bus import EventBus
    from embry0.services.issue_executor import IssueExecutor

    executor = IssueExecutor.__new__(IssueExecutor)
    executor._background_tasks = set()
    executor._tasks_by_job = {}
    executor._event_bus = EventBus()

    async def _work() -> None:
        await asyncio.sleep(0.1)

    task = executor._track_task(_work(), kind="workflow", job_id="job-1")
    assert executor._tasks_by_job.get("job-1") is task

    await task
    await asyncio.sleep(0)
    assert "job-1" not in executor._tasks_by_job


@pytest.mark.asyncio
async def test_cancel_job_cancels_running_task(monkeypatch):
    """cancel_job cancels the active task, purges sandbox/checkpoints/status."""
    from embry0.api.events.bus import EventBus
    from embry0.services.issue_executor import IssueExecutor

    executor = IssueExecutor.__new__(IssueExecutor)
    executor._background_tasks = set()
    executor._tasks_by_job = {}
    executor._event_bus = EventBus()
    executor._database_url = "postgres://stub"
    executor._audit_log_path = None
    executor._db = MagicMock()

    jobs = MagicMock()
    jobs.update = AsyncMock()
    jobs.get = AsyncMock(return_value={"job_id": "job-1", "issue_id": "iss-1"})
    executor._jobs = jobs

    issues = MagicMock()
    issues.update = AsyncMock()
    executor._issues = issues

    sandbox = MagicMock()
    sandbox.destroy = AsyncMock()
    executor._sandbox = sandbox

    # Stub purge_thread to avoid real DB
    import embry0.orchestration.checkpoint as ckpt_mod

    purge_calls: list[tuple[str, str]] = []

    async def _fake_purge(url: str, thread_id: str) -> None:
        purge_calls.append((url, thread_id))

    monkeypatch.setattr(ckpt_mod, "purge_thread", _fake_purge)

    # Stub emit_audit (imported into issue_executor)
    import embry0.services.issue_executor as exec_mod

    audit_calls: list[dict] = []

    async def _fake_audit(*args, **kwargs):
        audit_calls.append({"args": args, "kwargs": kwargs})

    monkeypatch.setattr(exec_mod, "emit_audit", _fake_audit)

    # Start a slow task that we'll cancel
    started = asyncio.Event()

    async def _slow() -> None:
        started.set()
        await asyncio.sleep(10)

    task = executor._track_task(_slow(), kind="workflow", job_id="job-1")
    await started.wait()

    await executor.cancel_job("job-1", actor="tester")

    assert task.cancelled()
    sandbox.destroy.assert_awaited_once_with("sandbox-job-1")
    jobs.update.assert_any_await("job-1", status="cancelled")
    issues.update.assert_any_await("iss-1", status="open")
    assert purge_calls == [("postgres://stub", "job-1")]
    assert any("job.cancelled" in str(a) for a in audit_calls)


@pytest.mark.asyncio
async def test_cancel_job_is_idempotent_when_no_task(monkeypatch):
    """cancel_job called for a job with no live task completes cleanly."""
    from embry0.services.issue_executor import IssueExecutor

    executor = IssueExecutor.__new__(IssueExecutor)
    executor._background_tasks = set()
    executor._tasks_by_job = {}
    executor._event_bus = None
    executor._database_url = "postgres://stub"
    executor._audit_log_path = None
    executor._db = MagicMock()

    jobs = MagicMock()
    jobs.update = AsyncMock()
    jobs.get = AsyncMock(return_value=None)
    executor._jobs = jobs

    issues = MagicMock()
    issues.update = AsyncMock()
    executor._issues = issues
    executor._sandbox = None  # no sandbox to destroy

    async def _fake_purge(url, tid):
        pass

    async def _fake_audit(*a, **k):
        pass

    import embry0.orchestration.checkpoint as ckpt_mod
    import embry0.services.issue_executor as exec_mod

    monkeypatch.setattr(ckpt_mod, "purge_thread", _fake_purge)
    monkeypatch.setattr(exec_mod, "emit_audit", _fake_audit)

    await executor.cancel_job("job-nonexistent")
    # Should not raise


@pytest.mark.asyncio
async def test_execute_generates_trace_id_and_passes_to_jobs_create(monkeypatch):
    """IssueExecutor.execute() generates a trc-* id and passes it via jobs.create."""
    from embry0.services.issue_executor import IssueExecutor

    executor = IssueExecutor.__new__(IssueExecutor)
    executor._background_tasks = set()
    executor._tasks_by_job = {}
    executor._event_bus = None
    executor._audit_log_path = None
    executor._db = MagicMock()

    issues = MagicMock()
    issues.get = AsyncMock(
        return_value={
            "id": "iss-1",
            "repo": "o/r",
            "title": "t",
            "body": None,
            "tracker": None,
            "tracker_key": None,
            "status": "triaging",
        }
    )
    executor._issues = issues

    jobs = MagicMock()
    jobs.list_all = AsyncMock(return_value=([], 0))
    jobs.create = AsyncMock(return_value="job-abc")
    executor._jobs = jobs

    # Stub emit_audit
    import embry0.services.issue_executor as exec_mod

    async def _fake_audit(*a, **k):
        pass

    monkeypatch.setattr(exec_mod, "emit_audit", _fake_audit)

    # Stub _track_task so we don't actually run the workflow
    def _fake_track(coro, *, kind, job_id, issue_id=None, launch=None):
        coro.close()  # prevent RuntimeWarning about unawaited coroutine
        return None

    executor._track_task = _fake_track  # type: ignore[method-assign]

    await executor.execute("iss-1")

    jobs.create.assert_awaited_once()
    call_kwargs = jobs.create.await_args.kwargs
    assert "trace_id" in call_kwargs
    assert call_kwargs["trace_id"].startswith("trc-")
    assert len(call_kwargs["trace_id"]) == 16  # "trc-" + 12 hex chars


@pytest.mark.asyncio
async def test_handle_interrupt_max_retries_pauses():
    """_handle_interrupt with reason=max_retries pauses the JOB but leaves
    the ISSUE at status=open. IssueStatus has no paused value (allowed:
    awaiting_input | cancelled | closed | in_progress | open), so paused
    is a job-only status; the issue is reset to open so the user can
    re-trigger triage."""
    from embry0.services.issue_executor import IssueExecutor

    executor = IssueExecutor.__new__(IssueExecutor)
    executor._jobs = MagicMock()
    executor._jobs.update = AsyncMock()
    executor._issues = MagicMock()
    executor._issues.update = AsyncMock()

    await executor._handle_interrupt("iss-1", "job-1", {"reason": "max_retries"})
    executor._jobs.update.assert_any_await("job-1", status="paused")
    executor._issues.update.assert_any_await("iss-1", status="open")


@pytest.mark.asyncio
async def test_handle_interrupt_no_value_sets_awaiting_input():
    """_handle_interrupt with no interrupt_value must set both to awaiting_input."""
    from embry0.services.issue_executor import IssueExecutor

    executor = IssueExecutor.__new__(IssueExecutor)
    executor._jobs = MagicMock()
    executor._jobs.update = AsyncMock()
    executor._issues = MagicMock()
    executor._issues.update = AsyncMock()

    await executor._handle_interrupt("iss-1", "job-1", None)
    executor._jobs.update.assert_any_await("job-1", status="awaiting_input")
    executor._issues.update.assert_any_await("iss-1", status="awaiting_input")


@pytest.mark.asyncio
async def test_auto_answerable_retriage_folds_answers_into_context() -> None:
    """When all triage questions are auto-answerable, execute() receives a
    non-empty additional_context containing the Q&A pairs."""
    from embry0.services.issue_executor import _fold_auto_answers_into_context

    # Unit-test the helper directly
    questions = [
        {"question": "Which database?", "importance": "auto_answerable", "auto_answer": "PostgreSQL"},
        {"question": "Use TypeScript?", "importance": "auto_answerable", "auto_answer": "Yes"},
    ]
    result = _fold_auto_answers_into_context(questions)
    assert "Which database?" in result
    assert "PostgreSQL" in result
    assert "Use TypeScript?" in result
    assert "Yes" in result


@pytest.mark.asyncio
async def test_fold_auto_answers_returns_empty_when_no_answers() -> None:
    """Helper returns empty string if no auto_answers are present."""
    from embry0.services.issue_executor import _fold_auto_answers_into_context

    questions = [
        {"question": "Which database?", "importance": "blocking", "auto_answer": None},
    ]
    result = _fold_auto_answers_into_context(questions)
    assert result == ""


@pytest.mark.asyncio
async def test_handle_needs_info_all_auto_passes_context_to_execute(monkeypatch) -> None:
    """When all questions are auto-answerable, _handle_needs_info must call
    execute() with a non-empty additional_context containing the Q&A text."""
    from embry0.services.issue_executor import IssueExecutor

    # Minimal fake DB with transaction support
    class _FakeConn:
        async def execute(self, query: str, *args):
            pass

    class _FakeDb:
        def transaction(self):
            conn = _FakeConn()

            class _TxnCM:
                async def __aenter__(self_inner):
                    return conn

                async def __aexit__(self_inner, exc_type, exc, tb):
                    return False

            return _TxnCM()

    executor = IssueExecutor.__new__(IssueExecutor)
    executor._db = _FakeDb()
    executor._inputs = MagicMock()
    executor._jobs = MagicMock()
    executor._jobs.update = AsyncMock()
    executor._issues = MagicMock()
    executor._issues.update = AsyncMock()
    executor._config = None
    executor._audit_log_path = None

    # Capture execute() calls
    captured_context: list[str] = []
    captured_actor: list[str | None] = []

    async def _fake_execute(issue_id: str, additional_context: str = "", dispatched_by: str | None = None) -> str:
        captured_context.append(additional_context)
        captured_actor.append(dispatched_by)
        return "job-new"

    executor.execute = _fake_execute  # type: ignore[method-assign]

    # Stub emit_audit
    import embry0.services.issue_executor as exec_mod

    monkeypatch.setattr(exec_mod, "emit_audit", AsyncMock())

    await executor._handle_needs_info(
        issue_id="iss-1",
        job_id="job-1",
        decision={
            "questions": [
                {
                    "question": "Which database?",
                    "importance": "auto_answerable",
                    "suggested_answer": "PostgreSQL",
                },
            ],
            "asking_node": "triage",
        },
    )

    assert len(captured_context) == 1, "execute() must be called exactly once"
    ctx = captured_context[0]
    assert ctx, "additional_context must be non-empty"
    assert "Which database?" in ctx
    assert "PostgreSQL" in ctx
    # #281: the re-triage is a continuation, not a person — it carries the
    # service actor so the row says who asked.
    assert captured_actor == ["embry0:auto-retriage"]


class _FakeGraph:
    """Minimal graph stub for _process_stream: replays canned astream events
    and reports no pending interrupt from aget_state."""

    def __init__(self, events):
        self._events = events

    async def astream(self, input_value, config=None, stream_mode=None):
        for event in self._events:
            yield event

    async def aget_state(self, config):
        return None  # falsy → no interrupt


def _stream_executor(jobs):
    from embry0.services.issue_executor import IssueExecutor

    executor = IssueExecutor.__new__(IssueExecutor)
    executor._jobs = jobs
    return executor


@pytest.mark.asyncio
async def test_process_stream_persists_current_stage_per_node_transition():
    """Each node update carrying a new current_stage writes it to the jobs row
    (the Console board reads it from the poll alone)."""
    jobs = MagicMock()
    jobs.update = AsyncMock()
    executor = _stream_executor(jobs)

    graph = _FakeGraph(
        [
            ("updates", {"init": {"current_stage": "initialized"}}),
            ("updates", {"triage": {"current_stage": "triage_complete"}}),
            ("updates", {"developer": {"current_stage": "developer_complete"}}),
        ]
    )
    final_state, interrupted, interrupt_value = await executor._process_stream(graph, {}, {}, "iss-1", "job-1")

    assert not interrupted
    assert final_state["current_stage"] == "developer_complete"
    stage_writes = [c.kwargs["current_stage"] for c in jobs.update.await_args_list]
    assert stage_writes == ["initialized", "triage_complete", "developer_complete"]
    assert all(c.args == ("job-1",) for c in jobs.update.await_args_list)


@pytest.mark.asyncio
async def test_process_stream_dedupes_repeated_and_skips_missing_stage():
    """No write when a node output repeats the last stage or omits/blanks it —
    one DB write per actual transition, not per event."""
    jobs = MagicMock()
    jobs.update = AsyncMock()
    executor = _stream_executor(jobs)

    graph = _FakeGraph(
        [
            ("updates", {"triage": {"current_stage": "triage_complete"}}),
            ("updates", {"noop": {"current_stage": "triage_complete"}}),  # repeat
            ("updates", {"other": {"agent_outputs": []}}),  # no stage key
            ("updates", {"blank": {"current_stage": ""}}),  # empty stage
        ]
    )
    await executor._process_stream(graph, {}, {}, "iss-1", "job-1")

    assert jobs.update.await_count == 1
    assert jobs.update.await_args.kwargs == {"current_stage": "triage_complete"}


@pytest.mark.asyncio
async def test_process_stream_stage_write_failure_does_not_break_stream(capsys):
    """The stage write is best-effort: a DB failure logs a warning and the
    stream (and final_state accumulation) continues."""
    jobs = MagicMock()
    jobs.update = AsyncMock(side_effect=RuntimeError("db down"))
    executor = _stream_executor(jobs)

    graph = _FakeGraph(
        [
            ("updates", {"triage": {"current_stage": "triage_complete"}}),
            ("updates", {"developer": {"current_stage": "developer_complete"}}),
        ]
    )
    final_state, interrupted, _ = await executor._process_stream(graph, {}, {}, "iss-1", "job-1")

    assert not interrupted
    assert final_state["current_stage"] == "developer_complete"
    assert "current_stage_persist_failed" in capsys.readouterr().out


# ---- RAV-1052: agent.question webhook emission ------------------------------


@pytest.mark.asyncio
async def test_handle_needs_info_emits_agent_question_webhooks():
    """One agent.question event per BLOCKING question, with a dashboard
    answer_url — emitted even when the issue row is gone (issue-less path
    uses /jobs/{id})."""
    from embry0.services.issue_executor import IssueExecutor

    class _FakeConn:
        async def fetchval(self, query, *args):
            return None

        async def execute(self, query, *args):
            return None

    class _FakeDb:
        def transaction(self):
            class _TxnCM:
                async def __aenter__(self_inner):
                    return _FakeConn()

                async def __aexit__(self_inner, exc_type, exc, tb):
                    return False

            return _TxnCM()

        async def execute(self, query, *args):
            return None

    executor = IssueExecutor.__new__(IssueExecutor)
    executor._db = _FakeDb()
    executor._inputs = MagicMock()
    executor._jobs = MagicMock(cas_transition_on_conn=AsyncMock(return_value=True))
    executor._issues = MagicMock(
        get=AsyncMock(return_value=None),
        cas_transition_on_conn=AsyncMock(return_value=True),
    )
    executor._config = None
    executor._audit_log_path = None
    notifier = MagicMock()
    executor._webhook_notifier = notifier

    with patch("embry0.services.issue_executor.emit_audit", new=AsyncMock()):
        await executor._handle_needs_info(
            issue_id="iss-1",
            job_id="job-1",
            decision={
                "questions": [
                    {"question": "Blocking Q?", "importance": "blocking"},
                    {"question": "Auto Q?", "importance": "auto_answerable", "auto_answer": "yes"},
                ],
                "asking_node": "triage",
            },
        )

    calls = notifier.emit_soon.call_args_list
    assert len(calls) == 1  # only the blocking question emits
    event, data = calls[0].args
    assert event == "agent.question"
    assert data["question"] == "Blocking Q?"
    assert data["job_id"] == "job-1"
    assert data["asking_node"] == "triage"
    assert data["answer_url"].endswith("/issues/iss-1")
    assert data["input_id"].startswith("inp-")
    # Issue row gone → identifier is None but the key is always present.
    assert data["linear_identifier"] is None


# ---- RAV-1076: Linear decision trail for agent questions --------------------


class _FakeTracker:
    """IssueTracker-protocol-conforming recording tracker (#163)."""

    def __init__(self, name="linear"):
        self._name = name
        self.posts = []

    @property
    def name(self):
        return self._name

    async def handle_webhook_event(self, payload, ctx):
        return {"status": "ignored"}

    async def post_issue_comment(self, ref, body):
        self.posts.append((ref, body))
        return True


def _linear_channel():
    """A real TrackerCommentChannel over a recording fake linear tracker."""
    from embry0.notifications.tracker_comment import TrackerCommentChannel
    from embry0.trackers import TrackerRegistry

    registry = TrackerRegistry()
    tracker = _FakeTracker("linear")
    registry.register(tracker)
    return TrackerCommentChannel(registry, "linear"), tracker


def _needs_info_executor(issue_row):
    """Executor via __new__ with just the attributes _handle_needs_info touches."""
    from embry0.services.issue_executor import IssueExecutor

    class _FakeConn:
        async def fetchval(self, query, *args):
            return None

        async def execute(self, query, *args):
            return None

    class _FakeDb:
        def transaction(self):
            class _TxnCM:
                async def __aenter__(self_inner):
                    return _FakeConn()

                async def __aexit__(self_inner, exc_type, exc, tb):
                    return False

            return _TxnCM()

        async def execute(self, query, *args):
            return None

    executor = IssueExecutor.__new__(IssueExecutor)
    executor._db = _FakeDb()
    executor._inputs = MagicMock()
    executor._jobs = MagicMock(cas_transition_on_conn=AsyncMock(return_value=True))
    executor._issues = MagicMock(
        get=AsyncMock(return_value=issue_row),
        cas_transition_on_conn=AsyncMock(return_value=True),
    )
    executor._config = None
    executor._audit_log_path = None
    executor._webhook_notifier = None
    channel, tracker = _linear_channel()
    executor._tracker_channels = {"linear": channel}
    executor._linear_tracker = tracker  # test-only handle onto the recording fake
    return executor


@pytest.mark.asyncio
async def test_handle_needs_info_posts_questions_to_linear():
    """Blocking questions land as one Linear comment on the linked issue."""
    issue_row = {"id": "iss-1", "tracker": "linear", "tracker_key": "ENG-9", "tracker_native_id": "uuid-9"}
    executor = _needs_info_executor(issue_row)

    with patch("embry0.services.issue_executor.emit_audit", new=AsyncMock()):
        await executor._handle_needs_info(
            issue_id="iss-1",
            job_id="job-1",
            decision={
                "questions": [
                    {"question": "Blocking Q?", "importance": "blocking"},
                    {"question": "Auto Q?", "importance": "auto_answerable", "auto_answer": "yes"},
                ],
                "asking_node": "developer",
            },
        )

    assert len(executor._linear_tracker.posts) == 1
    ref, body = executor._linear_tracker.posts[0]
    assert ref.native_id == "uuid-9"
    assert "embry0 needs input" in body
    assert "> Blocking Q?" in body
    assert "Auto Q?" not in body  # auto-answerable questions don't pause; not posted
    assert "job-1" in body
    assert "developer" in body


@pytest.mark.asyncio
async def test_handle_needs_info_no_linear_comment_without_linkage():
    """Issue not from Linear → no comment attempted."""
    executor = _needs_info_executor({"id": "iss-1", "tracker": None, "tracker_key": None, "tracker_native_id": None})

    with patch("embry0.services.issue_executor.emit_audit", new=AsyncMock()):
        await executor._handle_needs_info(
            issue_id="iss-1",
            job_id="job-1",
            decision={"questions": [{"question": "Q?", "importance": "blocking"}], "asking_node": "triage"},
        )

    assert executor._linear_tracker.posts == []


@pytest.mark.asyncio
async def test_handle_needs_info_webhook_carries_linear_identifier():
    """The agent.question payload includes the linked Linear identifier."""
    issue_row = {"id": "iss-1", "tracker": "linear", "tracker_key": "ENG-9", "tracker_native_id": "uuid-9"}
    executor = _needs_info_executor(issue_row)
    notifier = MagicMock()
    executor._webhook_notifier = notifier

    with patch("embry0.services.issue_executor.emit_audit", new=AsyncMock()):
        await executor._handle_needs_info(
            issue_id="iss-1",
            job_id="job-1",
            decision={"questions": [{"question": "Q?", "importance": "blocking"}], "asking_node": "triage"},
        )

    _event, data = notifier.emit_soon.call_args_list[0].args
    assert data["linear_identifier"] == "ENG-9"


# ---- RAV-1063: needs_info status race aborts quietly ------------------------


@pytest.mark.asyncio
async def test_handle_needs_info_aborts_on_status_race():
    """A cancel racing the interrupt: the job CAS finds the job no longer
    running → the whole transaction aborts, and NOTHING downstream fires (no
    audit rows, no agent.question webhooks, no channel dispatch, no Linear
    question comment). Previously the raw UPDATEs would clobber the
    cancelled status and strand input rows on a dead job."""
    issue_row = {"id": "iss-1", "tracker": "linear", "tracker_key": "ENG-9", "tracker_native_id": "uuid-9"}
    executor = _needs_info_executor(issue_row)
    executor._jobs.cas_transition_on_conn = AsyncMock(return_value=False)
    notifier = MagicMock()
    executor._webhook_notifier = notifier

    audit = AsyncMock()
    with patch("embry0.services.issue_executor.emit_audit", new=audit):
        await executor._handle_needs_info(
            issue_id="iss-1",
            job_id="job-1",
            decision={"questions": [{"question": "Q?", "importance": "blocking"}], "asking_node": "developer"},
        )

    notifier.emit_soon.assert_not_called()
    audit.assert_not_awaited()
    assert executor._linear_tracker.posts == []
    # The issue CAS must not run once the job claim has failed.
    executor._issues.cas_transition_on_conn.assert_not_awaited()


# ---- RAV-1271: zero-question needs_info fails loudly ------------------------


@pytest.mark.asyncio
async def test_handle_needs_info_zero_questions_fails_job():
    """An agent_needs_info interrupt with zero questions is a workflow routing
    bug. It must fail the job (with finished_at + ERR_EMPTY_INTERRUPT), never
    fall into the all-auto-answerable branch that marked the job completed
    without finished_at and spawned a pointless retriage."""
    issue_row = {"id": "iss-1", "tracker": None, "tracker_key": None, "tracker_native_id": None}
    executor = _needs_info_executor(issue_row)
    executor._jobs.update = AsyncMock()
    executor._issues.update = AsyncMock()
    executor.execute = AsyncMock()  # the retriage path must NOT run

    with patch("embry0.services.issue_executor.emit_audit", new=AsyncMock()):
        await executor._handle_needs_info(
            issue_id="iss-1",
            job_id="job-1",
            decision={"questions": [], "asking_node": "triage"},
        )

    executor._jobs.update.assert_awaited_once()
    kwargs = executor._jobs.update.await_args.kwargs
    assert kwargs["status"] == "failed"
    assert kwargs["error_code"] == "ERR_EMPTY_INTERRUPT"
    assert kwargs["finished_at"] is not None
    executor._issues.update.assert_awaited_once_with("iss-1", status="open")
    executor.execute.assert_not_awaited()
    assert executor._linear_tracker.posts == []


# ---- #163: _post_tracker_questions dedupe against dispatch_questions --------


@pytest.mark.asyncio
async def test_post_tracker_questions_skips_when_channel_explicitly_selected():
    """An issue whose notification_channels explicitly name the tracker gets
    its questions comment from dispatch_questions — the unconditional
    tracker-trail post must NOT double-comment."""
    issue_row = {
        "id": "iss-1",
        "tracker": "linear",
        "tracker_key": "ENG-9",
        "tracker_native_id": "uuid-9",
        "notification_channels": ["linear"],
    }
    executor = _needs_info_executor(issue_row)

    await executor._post_tracker_questions(
        issue_row, "job-1", "triage", [{"input_id": "inp-1", "question": "Q?", "importance": "blocking"}]
    )

    assert executor._linear_tracker.posts == []


@pytest.mark.asyncio
async def test_post_tracker_questions_posts_when_channels_not_selected():
    """Without an explicit notification_channels selection the tracker trail
    is unconditional — the comment lands exactly once."""
    issue_row = {
        "id": "iss-1",
        "tracker": "linear",
        "tracker_key": "ENG-9",
        "tracker_native_id": "uuid-9",
        "notification_channels": [],
    }
    executor = _needs_info_executor(issue_row)

    await executor._post_tracker_questions(
        issue_row, "job-1", "triage", [{"input_id": "inp-1", "question": "Q?", "importance": "blocking"}]
    )

    assert len(executor._linear_tracker.posts) == 1
    _ref, body = executor._linear_tracker.posts[0]
    assert "> Q?" in body
    assert "job-1" in body
