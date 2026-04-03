"""LangGraph checkpoint integration — PostgreSQL-backed state persistence."""

from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from psycopg_pool import AsyncConnectionPool


def create_checkpointer(database_url: str) -> AsyncPostgresSaver:
    """Create an AsyncPostgresSaver for LangGraph checkpointing.

    Creates a pool-backed saver without opening connections immediately.
    Call ``await saver.setup()`` before first use to run migrations.

    Usage:
        saver = create_checkpointer(config.database_url)
        async with saver:
            await saver.setup()
            graph = workflow.compile(checkpointer=saver)
            result = await graph.ainvoke(state, config={"configurable": {"thread_id": job_id}})
    """
    pool = AsyncConnectionPool(database_url, open=False)
    return AsyncPostgresSaver(conn=pool)
