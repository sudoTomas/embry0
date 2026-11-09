"""A job stopped by a drain resumes from its checkpoint in the next process (#307).

Against a real database and the real ``AsyncPostgresSaver``, through the
production ``_execute_workflow_stream``: the drain stops the job at a node
boundary, and a fresh process goes on from there without running the
finished node again, after killing the agent the old process left in the
sandbox and re-enrolling the sandbox with the proxies.
"""

from __future__ import annotations

import os
from collections.abc import AsyncIterator
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from langchain_core.runnables import RunnableConfig
from langgraph.graph import END, START, StateGraph

from embry0.orchestration.restart import ShutdownInterrupt
from embry0.orchestration.state import JobState
from embry0.services.issue_executor import IssueExecutor
from embry0.storage.database import DatabasePool
from embry0.storage.migrations.runner import run_migrations

# changes_schema: clean_database_url drops and migrates the schema, and the checkpointer creates its tables in
# it (#437). Unmarked, the check after the test failed it whenever a database test ran before it in its process.
pytestmark = [pytest.mark.requires_postgres, pytest.mark.changes_schema]

JOB = "job-drain-1"
CONTAINER = "sandbox-job-drain-1"


@pytest.fixture
async def clean_database_url() -> AsyncIterator[str]:
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


class _Nodes:
    """init -> developer -> END. ``init`` starts the drain once it has done
    its work, as a SIGTERM arriving during it would."""

    def __init__(self) -> None:
        self.ran: list[str] = []
        self.drain_after_init: IssueExecutor | None = None

    async def init(self, state: dict[str, Any], config: RunnableConfig) -> dict[str, Any]:
        self.ran.append("init")
        if self.drain_after_init is not None:
            self.drain_after_init._draining = True
        return {"sandbox_container_id": CONTAINER, "base_branch": "main", "current_stage": "init_complete"}

    async def developer(self, state: dict[str, Any], config: RunnableConfig) -> dict[str, Any]:
        self.ran.append("developer")
        return {"branch_name": "embry0/fix", "current_stage": "developer_complete"}

    def workflow(self) -> Any:
        nodes = self

        class _Workflow:
            def compile(self, config: dict[str, Any]) -> Any:
                builder = StateGraph(JobState)
                builder.add_node("init", nodes.init)  # type: ignore[type-var]
                builder.add_node("developer", nodes.developer)  # type: ignore[type-var]
                builder.add_edge(START, "init")
                builder.add_edge("init", "developer")
                builder.add_edge("developer", END)
                return builder.compile(checkpointer=config["checkpointer"])

        return _Workflow()


def _process(database_url: str, nodes: _Nodes, order: list[str]) -> IssueExecutor:
    """A fresh orchestrator process: the database URL, a sandbox manager, nothing else."""
    ex = IssueExecutor.__new__(IssueExecutor)
    ex._database_url = database_url
    ex._jobs = MagicMock()
    ex._jobs.update = AsyncMock()
    ex._broadcast_event = AsyncMock()  # type: ignore[method-assign]
    ex._select_workflow = lambda _pipeline: nodes.workflow()  # type: ignore[method-assign]
    ex._build_graph_config = lambda job_id, **_kw: {"configurable": {"thread_id": job_id}}  # type: ignore[method-assign]
    ex._forge_for = AsyncMock(return_value="github")  # type: ignore[method-assign]
    sandbox = MagicMock()
    sandbox.stop_agent = AsyncMock(side_effect=lambda c: order.append(f"stop_agent:{c}"))
    sandbox.reenroll = AsyncMock(side_effect=lambda c, **_kw: order.append(f"reenroll:{c}"))
    sandbox.destroy = AsyncMock(side_effect=lambda c: order.append(f"destroy:{c}"))
    ex._sandbox = sandbox
    return ex


async def test_a_drained_job_resumes_from_its_checkpoint_in_a_fresh_process(clean_database_url: str) -> None:
    nodes = _Nodes()
    order: list[str] = []
    first = _process(clean_database_url, nodes, order)
    nodes.drain_after_init = first

    with pytest.raises(ShutdownInterrupt):
        await first._execute_workflow_stream({"job_id": JOB, "repo": "acme/widgets", "task": "t"}, None, JOB)
    assert nodes.ran == ["init"], f"the drain must stop the job at the boundary after init; nodes run: {nodes.ran}"
    nodes.drain_after_init = None

    final, interrupted, _ = await _process(clean_database_url, nodes, order)._execute_workflow_stream(None, None, JOB)

    assert not interrupted
    assert nodes.ran.count("init") == 1, (
        f"init finished before the drain; resuming must not run it again. Nodes run: {nodes.ran}"
    )
    assert nodes.ran[-1] == "developer", f"the resumed job must go on to the next node; nodes run: {nodes.ran}"
    assert (final or {}).get("sandbox_container_id") == CONTAINER, (
        f"the resume must hand back the sandbox recorded before the stop: {final}"
    )
    assert order == [f"stop_agent:{CONTAINER}", f"reenroll:{CONTAINER}"], (
        "before the graph goes on, the agent the old process left in the sandbox is killed, then the sandbox "
        f"re-enrolled with the proxies; got {order}"
    )


async def test_a_job_stopped_inside_its_first_node_gets_a_fresh_sandbox(clean_database_url: str) -> None:
    """No sandbox in the checkpoint: init was cut off, maybe after creating the
    container. The leftover is removed so init can create it again."""
    nodes = _Nodes()
    order: list[str] = []
    ex = _process(clean_database_url, nodes, order)
    from embry0.orchestration.checkpoint import checkpointer_context

    async with checkpointer_context(clean_database_url) as saver:
        graph = nodes.workflow().compile({"checkpointer": saver})
        await graph.aupdate_state({"configurable": {"thread_id": JOB}}, {"job_id": JOB, "repo": "acme/widgets"})

    await ex._execute_workflow_stream(None, None, JOB)

    assert order[0] == f"destroy:sandbox-{JOB}", f"the half-made sandbox must be removed first; got {order}"
