import asyncpg
import pytest

from athanor.storage.database import DatabasePool
from athanor.storage.migrations.runner import run_migrations
from athanor.storage.repositories.pipeline_templates import PipelineTemplatesRepository

SAMPLE_GRAPH = {
    "graph_id": "g1",
    "name": "Test Pipeline",
    "nodes": [{"node_id": "n1", "agent_type": "developer"}],
    "edges": [],
}


@pytest.fixture
async def templates_repo(pg_pool: asyncpg.Pool) -> PipelineTemplatesRepository:
    import os

    url = os.environ.get("TEST_DATABASE_URL", "postgresql://athanor:athanor@localhost:5432/athanor_test")
    db = DatabasePool(url)
    await db.connect()
    await run_migrations(db)
    yield PipelineTemplatesRepository(db)
    await db.close()


@pytest.mark.asyncio
async def test_create_and_get(templates_repo: PipelineTemplatesRepository):
    template = await templates_repo.create(name="My Pipeline", graph_definition=SAMPLE_GRAPH)
    assert template is not None
    assert template["name"] == "My Pipeline"
    assert template["graph_definition"] == SAMPLE_GRAPH

    fetched = await templates_repo.get(template["id"])
    assert fetched is not None
    assert fetched["id"] == template["id"]
    assert fetched["graph_definition"] == SAMPLE_GRAPH


@pytest.mark.asyncio
async def test_list_all(templates_repo: PipelineTemplatesRepository):
    await templates_repo.create(name="Pipeline A", graph_definition=SAMPLE_GRAPH)
    await templates_repo.create(name="Pipeline B", graph_definition=SAMPLE_GRAPH)

    results = await templates_repo.list_all()
    assert len(results) >= 2
    names = {r["name"] for r in results}
    assert "Pipeline A" in names
    assert "Pipeline B" in names

    # list_all should not include graph_definition
    for r in results:
        assert "graph_definition" not in r


@pytest.mark.asyncio
async def test_update(templates_repo: PipelineTemplatesRepository):
    template = await templates_repo.create(name="Original", graph_definition=SAMPLE_GRAPH)
    template_id = template["id"]

    updated_graph = {**SAMPLE_GRAPH, "name": "Updated Pipeline"}
    updated = await templates_repo.update(template_id, name="Renamed", graph_definition=updated_graph)

    assert updated["name"] == "Renamed"
    assert updated["graph_definition"] == updated_graph


@pytest.mark.asyncio
async def test_delete(templates_repo: PipelineTemplatesRepository):
    template = await templates_repo.create(name="To Delete", graph_definition=SAMPLE_GRAPH)
    template_id = template["id"]

    await templates_repo.delete(template_id)
    assert await templates_repo.get(template_id) is None


@pytest.mark.asyncio
async def test_duplicate(templates_repo: PipelineTemplatesRepository):
    original = await templates_repo.create(name="Original", graph_definition=SAMPLE_GRAPH, sandbox_profile="default")
    copy = await templates_repo.duplicate(original["id"], new_name="Original (copy)")

    assert copy["id"] != original["id"]
    assert copy["name"] == "Original (copy)"
    assert copy["graph_definition"] == SAMPLE_GRAPH
    assert copy["sandbox_profile"] == "default"


@pytest.mark.asyncio
async def test_duplicate_nonexistent_raises(templates_repo: PipelineTemplatesRepository):
    with pytest.raises(ValueError, match="Pipeline template not found"):
        await templates_repo.duplicate("nonexistent-id", new_name="Doesn't Matter")


@pytest.mark.asyncio
async def test_get_nonexistent_returns_none(templates_repo: PipelineTemplatesRepository):
    assert await templates_repo.get("no-such-id") is None
