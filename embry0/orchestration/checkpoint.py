"""LangGraph checkpoint integration — PostgreSQL-backed state persistence."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver


@asynccontextmanager
async def checkpointer_context(database_url: str, *, setup: bool = True) -> AsyncIterator[AsyncPostgresSaver]:
    """Create an AsyncPostgresSaver as an async context manager.

    Usage:
        async with checkpointer_context(database_url) as saver:
            graph = workflow.compile(checkpointer=saver)
            result = await graph.ainvoke(state, config={"configurable": {"thread_id": job_id}})

    ``setup`` creates or migrates the saver's tables first, as a run does; a reader that only looks (an API
    request) passes False, so it never changes the schema.
    """
    async with AsyncPostgresSaver.from_conn_string(database_url) as saver:
        if setup:
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


async def sweep_orphan_checkpoints(database_url: str) -> int:
    """Delete checkpoint rows whose thread_id has no matching row in jobs.

    These are orphan rows left over when per-job purge was skipped (e.g.
    orchestrator crashed after writing job terminal status but before calling
    purge_thread). Runs once daily from the app lifespan background task.

    Returns the total number of checkpoint rows deleted across all three tables.
    """
    import asyncpg
    import structlog as _structlog

    _logger = _structlog.get_logger(__name__)

    conn = await asyncpg.connect(database_url)
    total = 0
    try:
        async with conn.transaction():
            for table in ("checkpoint_writes", "checkpoint_blobs", "checkpoints"):
                result = await conn.execute(f"DELETE FROM {table} WHERE thread_id NOT IN (SELECT job_id FROM jobs)")
                # result is a string like "DELETE 5"; parse the count
                try:
                    n = int(result.split()[-1])
                except (ValueError, IndexError):
                    n = 0
                total += n
        _logger.info("orphan_checkpoints_swept", total_deleted=total)
    finally:
        await conn.close()
    return total


async def checkpointed_threads(db: Any, thread_ids: list[str]) -> set[str]:
    """The ids among ``thread_ids`` that have a saved checkpoint (#307).

    Boot recovery resumes a running job from its checkpoint only when one
    exists. ``db`` is the shared ``DatabasePool``. The saver creates its tables
    on first use, so a deployment that never ran a graph has none: that is an
    empty set, not an error.
    """
    if not thread_ids:
        return set()
    if await db.fetchval("SELECT to_regclass('public.checkpoints') IS NOT NULL") is not True:
        return set()
    rows = await db.fetch(
        "SELECT DISTINCT thread_id FROM checkpoints WHERE thread_id = ANY($1::text[])",
        thread_ids,
    )
    return {row["thread_id"] for row in rows}
