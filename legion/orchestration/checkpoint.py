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


async def purge_thread(database_url: str, thread_id: str) -> None:
    """Delete all checkpoint rows for ``thread_id``.

    Used when a job is cancelled or a paused job expires — its saved graph
    state is no longer useful and should not accumulate in the database.

    Uses the underlying asyncpg connection directly because
    ``AsyncPostgresSaver`` doesn't yet expose a public ``adelete_thread`` in
    every release. The three tables written by the saver are
    ``checkpoints``, ``checkpoint_blobs``, and ``checkpoint_writes``; all
    three are scoped by ``thread_id``.
    """
    import asyncpg
    import structlog

    logger = structlog.get_logger(__name__)

    conn = await asyncpg.connect(database_url)
    try:
        async with conn.transaction():
            await conn.execute("DELETE FROM checkpoint_writes WHERE thread_id = $1", thread_id)
            await conn.execute("DELETE FROM checkpoint_blobs WHERE thread_id = $1", thread_id)
            await conn.execute("DELETE FROM checkpoints WHERE thread_id = $1", thread_id)
        logger.info("checkpoint_thread_purged", thread_id=thread_id)
    finally:
        await conn.close()
