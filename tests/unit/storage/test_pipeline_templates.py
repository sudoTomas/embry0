import pytest

from athanor.storage.database import DatabasePool
from athanor.storage.repositories.pipeline_templates import PipelineTemplatesRepository

pytestmark = pytest.mark.requires_postgres

SAMPLE_GRAPH = {
    "graph_id": "g1",
    "name": "Test Pipeline",
    "nodes": [{"node_id": "n1", "agent_type": "developer"}],
    "edges": [],
}


@pytest.fixture
async def templates_repo(db_with_migrations: DatabasePool) -> PipelineTemplatesRepository:
    return PipelineTemplatesRepository(db_with_migrations)


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
async def test_upsert_builtin_inserts_with_is_builtin_true(
    templates_repo: PipelineTemplatesRepository,
):
    seed = await templates_repo.upsert_builtin(
        name="seed-quick-fix",
        description="Triage then developer.",
        graph_definition=SAMPLE_GRAPH,
        sandbox_profile="slim",
    )
    assert seed["name"] == "seed-quick-fix"
    assert seed["is_builtin"] is True
    assert seed["graph_definition"] == SAMPLE_GRAPH
    assert seed["sandbox_profile"] == "slim"


@pytest.mark.asyncio
async def test_upsert_builtin_is_idempotent_on_repeated_calls(
    templates_repo: PipelineTemplatesRepository,
):
    first = await templates_repo.upsert_builtin(
        name="seed-repeat",
        graph_definition=SAMPLE_GRAPH,
    )
    updated_graph = {**SAMPLE_GRAPH, "description": "second pass"}
    second = await templates_repo.upsert_builtin(
        name="seed-repeat",
        graph_definition=updated_graph,
    )
    assert first["id"] == second["id"]
    assert second["graph_definition"] == updated_graph
    assert second["is_builtin"] is True


@pytest.mark.asyncio
async def test_upsert_builtin_overwrites_user_template_under_same_name(
    templates_repo: PipelineTemplatesRepository,
):
    user_template = await templates_repo.create(
        name="seed-collision",
        graph_definition=SAMPLE_GRAPH,
        description="user version",
    )
    assert user_template["is_builtin"] is False

    after_seed = await templates_repo.upsert_builtin(
        name="seed-collision",
        graph_definition=SAMPLE_GRAPH,
        description="seed version",
    )
    assert after_seed["id"] == user_template["id"]
    assert after_seed["description"] == "seed version"
    assert after_seed["is_builtin"] is True


@pytest.mark.asyncio
async def test_list_all_includes_is_builtin_and_orders_seeds_first(
    templates_repo: PipelineTemplatesRepository,
):
    await templates_repo.create(name="zzzz-user", graph_definition=SAMPLE_GRAPH)
    await templates_repo.upsert_builtin(name="aaaa-seed", graph_definition=SAMPLE_GRAPH)

    rows = await templates_repo.list_all()
    is_builtin_seen_user = False
    for row in rows:
        assert "is_builtin" in row
        if not row["is_builtin"]:
            is_builtin_seen_user = True
        elif is_builtin_seen_user:
            pytest.fail(
                f"Builtin {row['name']!r} appeared after a non-builtin row — list_all should sort builtins first."
            )


@pytest.mark.asyncio
async def test_get_nonexistent_returns_none(templates_repo: PipelineTemplatesRepository):
    assert await templates_repo.get("no-such-id") is None
