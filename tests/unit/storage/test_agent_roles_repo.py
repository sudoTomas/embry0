"""An agent's roles as the repository writes and reads them (#395)."""

from __future__ import annotations

import pytest

from embry0.storage.database import DatabasePool
from embry0.storage.repositories.agent_definitions import BUILTIN_SEED, AgentDefinitionsRepository, coders_on
from embry0.storage.seeds.agents_builtin import seed_missing_builtin_agents

pytestmark = [pytest.mark.requires_postgres]


async def test_the_seed_writes_the_built_in_coders_role_and_reset_puts_it_back(
    db_with_migrations: DatabasePool,
) -> None:
    repo = AgentDefinitionsRepository(db_with_migrations)
    await db_with_migrations.execute("DELETE FROM agent_definitions")
    await seed_missing_builtin_agents(repo)
    roles = {str(r["type"]): list(r["roles"]) for r in await repo.list_all()}
    assert {name: held for name, held in roles.items() if held} == {
        "developer": ["coder"],
        "slopmaster": ["coder"],
        "triage": ["docs"],
        "review": ["docs"],
        "context-auditor": ["docs"],
    }, roles
    assert BUILTIN_SEED["developer"]["roles"] == ["coder"]

    await db_with_migrations.execute("UPDATE agent_definitions SET roles = '{}' WHERE type IN ('slopmaster', 'review')")
    assert (await repo.reset("slopmaster"))["roles"] == ["coder"], "reset puts the seed's role back"
    assert (await repo.reset("review"))["roles"] == ["docs"]
    assert (await repo.reset("kintsugi"))["roles"] == [], "and gives none to a built-in the seed gives none"


async def test_a_new_agent_holds_only_the_roles_it_is_given(db_with_migrations: DatabasePool) -> None:
    repo = AgentDefinitionsRepository(db_with_migrations)
    assert (await repo.create(agent_type="scout", description="d", model="m"))["roles"] == []
    assert (await repo.create(agent_type="scribe", description="d", model="m", roles=["coder"]))["roles"] == ["coder"]
    with pytest.raises(ValueError, match="roles must be a list of coder"):
        await repo.create(agent_type="bard", description="d", model="m", roles=["bard"])


async def test_the_coders_are_read_from_the_rows_a_job_runs(db_with_migrations: DatabasePool) -> None:
    db = db_with_migrations
    repo = AgentDefinitionsRepository(db)
    await db.execute("DELETE FROM agent_definitions")
    async with db.transaction() as conn:
        assert await coders_on(conn) == {"developer", "slopmaster"}, "a built-in coder with no row is its seed's"
    await repo.create(agent_type="developer", description="d", model="m", roles=["coder"])
    await repo.create(agent_type="slopmaster", description="d", model="m")  # a row without the role
    await repo.create(agent_type="scribe", description="d", model="m", roles=["coder"])
    await repo.create(agent_type="voice", description="d", model="m", roles=["coder"], runs_in_jobs=False)
    async with db.transaction() as conn:
        assert await coders_on(conn, share=True) == {"developer", "scribe"}
