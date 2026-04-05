"""LangGraph checkpoint integration — PostgreSQL-backed state persistence."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver


@asynccontextmanager
async def checkpointer_context(database_url: str) -> AsyncIterator[AsyncPostgresSaver]:
    """Create an AsyncPostgresSaver as an async context manager.

    Usage:
        async with checkpointer_context(database_url) as saver:
            graph = workflow.compile(checkpointer=saver)
            result = await graph.ainvoke(state, config={"configurable": {"thread_id": job_id}})
    """
    async with AsyncPostgresSaver.from_conn_string(database_url) as saver:
        await saver.setup()
        yield saver
