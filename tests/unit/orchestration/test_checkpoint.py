import pytest

from legion.orchestration.checkpoint import create_checkpointer


@pytest.mark.asyncio
async def test_create_checkpointer_returns_saver():
    from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
    saver = create_checkpointer("postgresql://legion:legion@localhost:5432/legion")
    assert isinstance(saver, AsyncPostgresSaver)
