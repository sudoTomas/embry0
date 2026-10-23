"""Startup seeding of the specialist agents and their routes, against Postgres."""

import pytest

from embry0.agents.identity import BUILTIN_IDENTITY
from embry0.storage.database import DatabasePool
from embry0.storage.repositories.agent_definitions import BUILTIN_SEED, AgentDefinitionsRepository
from embry0.storage.repositories.pipeline_templates import PipelineTemplatesRepository
from embry0.storage.seeds.agents_builtin import seed_missing_builtin_agents
from embry0.storage.seeds.pipeline_templates_builtin import seed_builtin_pipeline_templates
from embry0.workflows._validation import validate_graph_definition

pytestmark = pytest.mark.requires_postgres

# Agent type -> the template that routes a job to it.
SPECIALISTS = {
    "kintsugi": "kintsugi-trace",
    "gardener": "garden",
    "interrogator": "interrogate",
    "why": "why",
    "how": "how",
    "blast-radius": "blast-radius",
    "comment-sicko": "comment-pass",
    "design-auditor": "design-audit",
    "context-auditor": "context-audit",
}
_FIELDS = ("description", "model", "tools", "skills", "system_prompt", "execution_mode", "auth_mode", "mcp_servers")


async def _specialist_rows(db: DatabasePool) -> list[dict]:
    rows = await db.fetch(
        "SELECT * FROM agent_definitions WHERE type = ANY($1::text[]) ORDER BY type",
        list(SPECIALISTS),
    )
    return [dict(r) for r in rows]


@pytest.mark.asyncio
async def test_startup_seeding_inserts_each_specialist_once_as_builtin(db_with_migrations: DatabasePool):
    assert await _specialist_rows(db_with_migrations) == [], "migrations alone must not carry them"
    repo = AgentDefinitionsRepository(db_with_migrations)

    await seed_missing_builtin_agents(repo)
    await seed_missing_builtin_agents(repo)  # a second startup

    rows = await _specialist_rows(db_with_migrations)
    assert [r["type"] for r in rows] == sorted(SPECIALISTS)
    for row in rows:
        assert row["is_builtin"] is True, row["type"]
        assert {f: row[f] for f in _FIELDS} == {f: BUILTIN_SEED[row["type"]][f] for f in _FIELDS}, row["type"]


@pytest.mark.asyncio
async def test_a_specialist_newer_than_migration_106_gets_its_identity_from_the_seeder(
    db_with_migrations: DatabasePool,
):
    """Migration 106 wrote the identity of the eight specialists it knew, and only into rows that
    existed. A deployment that already ran it gains context-auditor at its next start as a missing
    row, so the identity has to come with the insert."""
    db = db_with_migrations
    assert await db.fetchval("SELECT 1 FROM embry0_migrations WHERE version = 106") == 1
    repo = AgentDefinitionsRepository(db)
    await seed_missing_builtin_agents(repo)
    # The deployment as the release before this one left it: the eight, and no ninth row.
    await db.execute("DELETE FROM agent_definitions WHERE type = 'context-auditor'")

    await seed_missing_builtin_agents(repo)

    row = await repo.get("context-auditor")
    assert row is not None, "the seeder did not insert the missing specialist"
    assert row["is_builtin"] is True
    identity = BUILTIN_IDENTITY["context-auditor"]
    assert {f: row[f] for f in identity} == identity, "the seeded row is not at the built-in identity"


@pytest.mark.asyncio
async def test_startup_seeding_keeps_an_operator_edit(db_with_migrations: DatabasePool):
    repo = AgentDefinitionsRepository(db_with_migrations)
    await seed_missing_builtin_agents(repo)
    await repo.update("why", model="claude-haiku-4-5")

    await seed_missing_builtin_agents(repo)

    row = await repo.get("why")
    assert row is not None and row["model"] == "claude-haiku-4-5"


@pytest.mark.asyncio
async def test_reset_restores_an_edited_specialist(db_with_migrations: DatabasePool):
    repo = AgentDefinitionsRepository(db_with_migrations)
    await seed_missing_builtin_agents(repo)
    await repo.update("kintsugi", model="claude-haiku-4-5", tools=["Read", "Write"], system_prompt="edited")

    restored = await repo.reset("kintsugi")

    assert {f: restored[f] for f in _FIELDS} == {f: BUILTIN_SEED["kintsugi"][f] for f in _FIELDS}
    assert "Write" not in restored["tools"]


@pytest.mark.asyncio
async def test_a_specialist_cannot_be_deleted(db_with_migrations: DatabasePool):
    repo = AgentDefinitionsRepository(db_with_migrations)
    await seed_missing_builtin_agents(repo)

    with pytest.raises(ValueError, match="built-in"):
        await repo.delete("gardener")
    assert await repo.get("gardener") is not None


@pytest.mark.asyncio
async def test_template_seeding_is_idempotent_and_every_route_validates(db_with_migrations: DatabasePool):
    repo = PipelineTemplatesRepository(db_with_migrations)

    await seed_builtin_pipeline_templates(repo)
    await seed_builtin_pipeline_templates(repo)

    rows = {r["name"]: r for r in await repo.list_all() if r["name"] in SPECIALISTS.values()}
    assert sorted(rows) == sorted(SPECIALISTS.values())
    for agent_type, name in SPECIALISTS.items():
        summary = rows[name]
        assert summary["is_builtin"] is True, name
        assert summary["default_for_kind"] is None, name
        full = await repo.get(summary["id"])
        assert full is not None
        graph = full["graph_definition"]
        assert validate_graph_definition(name, graph) == []
        assert [n["agent_type"] for n in graph["nodes"]] == ["triage", agent_type, "output"]
