"""The Postgres checkpointer, exercised against a real database (#496).

A paused job resumes from its checkpoint, sometimes days later and after a
deploy has replaced the orchestrator process. These tests are the suite's only
proof that the state a job saved in Postgres comes back intact: they write
through the real ``AsyncPostgresSaver`` and read back through a fresh
``checkpointer_context``, as the next process would.

They used to skip whenever anything went wrong, so they skipped on every run
and measured nothing. A missing database is now an ERROR
(``ConnectionRefusedError``), like the storage tests, never a skip.
"""

from __future__ import annotations

import os
from collections.abc import AsyncIterator
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import asyncpg
import pytest
from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command, StateSnapshot

from embry0.orchestration.checkpoint import checkpointer_context, purge_thread
from embry0.orchestration.nodes.agent import _enforce_ask_user_cap
from embry0.orchestration.state import JobState
from embry0.services.issue_executor import IssueExecutor
from embry0.storage.database import DatabasePool
from embry0.storage.migrations.runner import run_migrations
from embry0.workflows.issue_to_pr.nodes import _extract_ask_user_events, ask_user_interrupt

# The checkpointer creates its own tables in the schema the migrations made.
pytestmark = [pytest.mark.requires_postgres, pytest.mark.changes_schema]

# The tables purge_thread clears, all written by the saver.
CHECKPOINT_TABLES = ("checkpoints", "checkpoint_blobs", "checkpoint_writes")

JOB = "job-ckpt-1"
REPO = "acme/widgets"
TASK = "Fix the widget count"
BRANCH = "embry0/iss-1-fix-the-widget-count"
BASE = "develop"
CONTAINER = "sandbox-job-ckpt-1"
ASK_EVENT = {
    "type": "agent_ask_user",
    "question": "Which branch should the fix target?",
    "category": "scope",
    "options": ["main", "develop"],
}
# The pending question exactly as production shapes it from the agent's event.
QUESTION = _extract_ask_user_events({"events": [ASK_EVENT]}, "developer")[0]
ANSWERS = [{"question": QUESTION["question"], "answer": "develop"}]
ASKED = {"agent_type": "developer", "is_error": False, "output": "asked which branch"}
DONE = {"agent_type": "developer", "is_error": False, "output": "fixed on develop"}


@pytest.fixture
async def clean_database_url() -> AsyncIterator[str]:
    """TEST_DATABASE_URL on a clean, fully migrated ``public`` schema.

    ``db_with_migrations`` without its skip: an unreachable database raises
    here, so the test ERRORS instead of reporting a skip nobody reads.
    """
    url = os.environ.get("TEST_DATABASE_URL", "postgresql://embry0:embry0@localhost:5432/embry0_test")
    db = DatabasePool(url, min_size=1, max_size=1)
    await db.connect()
    try:
        await db.execute("DROP SCHEMA public CASCADE")
        await db.execute("CREATE SCHEMA public")
        await run_migrations(db)
    finally:
        await db.close()
    yield url


async def _init(state: dict[str, Any], config: RunnableConfig) -> dict[str, Any]:
    return {"sandbox_container_id": CONTAINER, "base_branch": BASE, "current_stage": "init_complete"}


async def _developer(state: dict[str, Any], config: RunnableConfig) -> Command[str]:
    """Asks one question on its first visit; finishes once the answer is back."""
    answers = state.get("user_answers")
    if not answers:
        _, cap_updates = _enforce_ask_user_cap(state, [QUESTION])
        return Command(
            goto="ask_user_interrupt",
            update={
                **cap_updates,
                "branch_name": BRANCH,
                "current_stage": "developer_asked_user",
                "agent_outputs": [ASKED],
            },
        )
    return Command(
        goto=END,
        update={
            "developer_summary": f"Fixed on {answers[0]['answer']}",
            "user_answers": None,
            "current_stage": "developer_complete",
            "agent_outputs": [DONE],
        },
    )


def _graph() -> StateGraph[JobState]:
    """init -> developer -> the production ask_user_interrupt -> back to developer."""
    builder = StateGraph(JobState)
    builder.add_node("init", _init)  # type: ignore[type-var]
    builder.add_node("developer", _developer, destinations=("ask_user_interrupt", END))  # type: ignore[type-var]
    builder.add_node("ask_user_interrupt", ask_user_interrupt, destinations=("developer", END))  # type: ignore[type-var]
    builder.add_edge(START, "init")
    builder.add_edge("init", "developer")
    return builder


class _Workflow:
    def compile(self, config: dict[str, Any]) -> Any:
        return _graph().compile(checkpointer=config["checkpointer"])


def _process(database_url: str) -> IssueExecutor:
    """A fresh orchestrator process: it knows the database URL and nothing else.

    The graph runs through the production ``_execute_workflow_stream``, which
    opens its own ``checkpointer_context`` per call.
    """
    ex = IssueExecutor.__new__(IssueExecutor)
    ex._database_url = database_url
    ex._sandbox = None
    ex._jobs = MagicMock()
    ex._jobs.update = AsyncMock()
    ex._broadcast_event = AsyncMock()  # type: ignore[method-assign]
    ex._select_workflow = lambda _pipeline: _Workflow()  # type: ignore[method-assign,assignment]
    ex._build_graph_config = lambda job_id, **_kw: {"configurable": {"thread_id": job_id}}  # type: ignore[method-assign]
    return ex


async def _pause(database_url: str, job_id: str = JOB) -> None:
    """Run a job until it pauses on its question."""
    state = {"job_id": job_id, "repo": REPO, "task": TASK}
    final, interrupted, _ = await _process(database_url)._execute_workflow_stream(state, None, job_id)
    assert interrupted, (
        "the job must pause at ask_user_interrupt, and the executor learns that from the checkpoint it "
        f"just wrote; it reported no pause. Streamed state: {final}"
    )


async def _read_back(database_url: str, job_id: str = JOB) -> StateSnapshot:
    """The job's state as a new process finds it in Postgres."""
    async with checkpointer_context(database_url) as saver:
        graph = _graph().compile(checkpointer=saver)
        return await graph.aget_state({"configurable": {"thread_id": job_id}})


def _differences(expected: dict[str, Any], values: dict[str, Any]) -> dict[str, Any]:
    return {
        key: {"expected": want, "found": values.get(key, "<missing>")}
        for key, want in expected.items()
        if values.get(key, "<missing>") != want
    }


async def _rows_per_table(database_url: str, thread_id: str) -> dict[str, int]:
    conn = await asyncpg.connect(database_url)
    try:
        return {
            table: await conn.fetchval(f"SELECT count(*) FROM {table} WHERE thread_id = $1", thread_id)
            for table in CHECKPOINT_TABLES
        }
    finally:
        await conn.close()


async def test_checkpointer_context_creates_saver(clean_database_url: str) -> None:
    async with checkpointer_context(clean_database_url) as saver:
        assert isinstance(saver, AsyncPostgresSaver), f"checkpointer_context yielded {type(saver).__name__}"

    conn = await asyncpg.connect(clean_database_url)
    try:
        missing = [table for table in CHECKPOINT_TABLES if await conn.fetchval("SELECT to_regclass($1)", table) is None]
    finally:
        await conn.close()
    assert not missing, f"checkpointer_context must set up the saver's tables; missing after it ran: {missing}"


async def test_a_paused_job_reads_back_from_postgres_in_a_fresh_process(clean_database_url: str) -> None:
    await _pause(clean_database_url)

    snapshot = await _read_back(clean_database_url)

    assert snapshot.next == ("ask_user_interrupt",), (
        f"a new process must find the job paused at ask_user_interrupt; next={snapshot.next!r}, "
        f"values={snapshot.values!r}"
    )
    interrupts = [interrupt.value for task in snapshot.tasks for interrupt in task.interrupts]
    assert interrupts == [{"asking_node": "developer", "questions": [QUESTION], "reason": "agent_needs_info"}], (
        f"the pending question must come back from Postgres as the interrupt it was raised with: {interrupts!r}"
    )
    expected = {
        "job_id": JOB,
        "repo": REPO,
        "task": TASK,
        "sandbox_container_id": CONTAINER,
        "base_branch": BASE,
        "branch_name": BRANCH,
        "current_stage": "developer_asked_user",
        "pending_agent_questions": [QUESTION],
        "agent_question_rounds": 1,
        "agent_outputs": [ASKED],
    }
    lost = _differences(expected, snapshot.values)
    assert not lost, f"state written before the pause did not come back from Postgres: {lost}"


async def test_a_paused_job_resumes_to_completion_in_a_fresh_process(clean_database_url: str) -> None:
    await _pause(clean_database_url)

    final, interrupted, interrupt_value = await _process(clean_database_url)._execute_workflow_stream(
        Command(resume=ANSWERS), None, JOB
    )

    assert not interrupted, f"the resumed job must not pause again: interrupt={interrupt_value!r}"
    pre_pause = {"branch_name": BRANCH, "base_branch": BASE, "sandbox_container_id": CONTAINER}
    carried = _differences(pre_pause, final or {})
    assert not carried, f"the resume must hand back the facts set before the pause, read from the checkpoint: {carried}"

    snapshot = await _read_back(clean_database_url)
    assert snapshot.next == (), f"the resumed job must run to the end; it stopped before {snapshot.next!r}"
    expected = {
        "job_id": JOB,
        "repo": REPO,
        "task": TASK,
        "sandbox_container_id": CONTAINER,
        "base_branch": BASE,
        "branch_name": BRANCH,
        "developer_summary": "Fixed on develop",
        "current_stage": "developer_complete",
        "pending_agent_questions": [],
        "agent_question_rounds": 1,
        "agent_outputs": [ASKED, DONE],
    }
    wrong = _differences(expected, snapshot.values)
    assert not wrong, f"the finished job's state, as a new process reads it from Postgres, is wrong: {wrong}"


async def test_purge_thread_is_idempotent_for_nonexistent_thread(clean_database_url: str) -> None:
    """purge_thread on a thread_id with no rows completes cleanly — idempotent
    and safe to call on an already-purged / never-written thread."""
    async with checkpointer_context(clean_database_url):
        pass  # setup runs inside the context manager

    await purge_thread(clean_database_url, "thread-does-not-exist")
    await purge_thread(clean_database_url, "thread-does-not-exist")


async def test_purge_thread_deletes_only_target_thread_rows(clean_database_url: str) -> None:
    """purge_thread removes a paused job's rows from all three checkpoint
    tables and leaves another paused job's rows intact."""
    target, other = "job-to-purge", "job-to-keep"
    await _pause(clean_database_url, target)
    await _pause(clean_database_url, other)
    before = {thread: await _rows_per_table(clean_database_url, thread) for thread in (target, other)}
    empty = {thread: [t for t, n in counts.items() if n == 0] for thread, counts in before.items()}
    assert not any(empty.values()), f"a paused job must leave rows in every checkpoint table; empty: {empty}"

    await purge_thread(clean_database_url, target)

    after = {thread: await _rows_per_table(clean_database_url, thread) for thread in (target, other)}
    assert after[target] == dict.fromkeys(CHECKPOINT_TABLES, 0), (
        f"purge_thread left rows of the purged thread: {after[target]}"
    )
    assert after[other] == before[other], (
        f"purge_thread must not touch another thread: before {before[other]}, after {after[other]}"
    )
