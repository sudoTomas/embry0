"""Migration 142 — ``agent_definitions.triage_may_add_skills``, the skills triage may add to an agent.

The number is ``VERSION`` alone: renumbering the migration changes that line and this file's name.
"""

from __future__ import annotations

import asyncpg
import pytest

from embry0.storage.database import DatabasePool
from embry0.storage.migrations.runner import MIGRATIONS, run_migrations

#: This migration's number.
VERSION = 142

#: What the built-in developer may get from triage, as the triage prompt always offered it.
DEVELOPER = ["superpowers:brainstorming", "superpowers:writing-plans"]


def test_the_triage_may_add_migration_is_the_last_entry() -> None:
    """The newest migration owns this assertion; it passes to the next one
    (it came from tests/unit/storage/test_migration_141.py)."""
    assert MIGRATIONS[-1][0] == VERSION, f"migration {VERSION} must add agent_definitions.triage_may_add_skills"
    assert [v for v, _, _ in MIGRATIONS].count(VERSION) == 1, f"migration {VERSION} must be listed once"


async def _may_add(db: DatabasePool) -> dict[str, list[str]]:
    rows = await db.fetch("SELECT type, triage_may_add_skills FROM agent_definitions ORDER BY type")
    return {str(r["type"]): list(r["triage_may_add_skills"]) for r in rows}


async def _undo(db: DatabasePool) -> None:
    """The database as it stood before the migration."""
    await db.execute("ALTER TABLE agent_definitions DROP COLUMN triage_may_add_skills")
    await db.execute("DELETE FROM embry0_migrations WHERE version = $1", VERSION)


@pytest.mark.requires_postgres
async def test_a_fresh_database_lets_triage_add_only_the_developers_two_skills(
    db_with_migrations: DatabasePool,
) -> None:
    """Migrating from empty: the built-in developer a migration inserted gets the two skills, every other row
    none, and the seeder that adds the remaining built-ins writes each one's seed value."""
    from embry0.storage.repositories.agent_definitions import BUILTIN_SEED, AgentDefinitionsRepository
    from embry0.storage.seeds.agents_builtin import seed_missing_builtin_agents

    db = db_with_migrations
    migrated = await _may_add(db)
    assert migrated.get("developer") == DEVELOPER, f"the migrated developer row: {migrated.get('developer')}"
    others = {t: v for t, v in migrated.items() if t != "developer" and v}
    assert others == {}, f"every other migrated row gets none: {others}"

    await seed_missing_builtin_agents(AgentDefinitionsRepository(db))
    seeded = await _may_add(db)
    expected = {t: list(BUILTIN_SEED[t].get("triage_may_add_skills", [])) for t in seeded if t in BUILTIN_SEED}
    assert {t: seeded[t] for t in expected} == expected, f"each built-in holds its seed value: {seeded}"
    assert [t for t, v in seeded.items() if v] == ["developer"], f"only the developer may get skills: {seeded}"


@pytest.mark.requires_postgres
async def test_an_upgrade_sets_the_developer_and_leaves_every_other_row_empty(db_with_migrations: DatabasePool) -> None:
    db = db_with_migrations
    await _undo(db)
    await db.execute("UPDATE agent_definitions SET skills = '[\"local:house-style\"]'::jsonb WHERE type = 'developer'")
    await db.execute(
        "INSERT INTO agent_definitions (type, description, model, tools, skills, system_prompt, is_builtin) "
        "VALUES ('custom-agent', 'd', 'm', '[]', '[\"superpowers:brainstorming\"]', '', false)"
    )
    await run_migrations(db)

    after = await _may_add(db)
    assert after["developer"] == DEVELOPER, f"the developer keeps today's behaviour: {after['developer']}"
    assert after["custom-agent"] == [], f"any other row gets none: {after['custom-agent']}"
    assert [t for t, v in after.items() if v] == ["developer"], f"only the developer may get skills: {after}"
    developer = await db.fetchrow("SELECT skills FROM agent_definitions WHERE type = 'developer'")
    assert list(developer["skills"]) == ["local:house-style"], "the migration changes no operator's skills"

    column = await db.fetchrow(
        "SELECT data_type, is_nullable, column_default FROM information_schema.columns "
        "WHERE table_name = 'agent_definitions' AND column_name = 'triage_may_add_skills'"
    )
    assert column is not None and (column["data_type"], column["is_nullable"]) == ("jsonb", "NO"), column
    assert column["column_default"] == "'[]'::jsonb", f"a new row may get none: {column['column_default']}"
    with pytest.raises(asyncpg.CheckViolationError):
        await db.execute("UPDATE agent_definitions SET triage_may_add_skills = '{}'::jsonb WHERE type = 'developer'")


@pytest.mark.requires_postgres
async def test_an_upgrade_without_a_developer_row_creates_none(db_with_migrations: DatabasePool) -> None:
    db = db_with_migrations
    await _undo(db)
    await db.execute("DELETE FROM agent_definitions WHERE type = 'developer'")
    await run_migrations(db)
    assert "developer" not in await _may_add(db), "the migration only sets a developer row that exists"


@pytest.mark.requires_postgres
async def test_a_developer_of_the_deployments_own_is_left_empty(db_with_migrations: DatabasePool) -> None:
    """Only the built-in developer gets the two skills."""
    db = db_with_migrations
    await _undo(db)
    await db.execute("UPDATE agent_definitions SET is_builtin = false WHERE type = 'developer'")
    await run_migrations(db)
    assert (await _may_add(db))["developer"] == [], "a developer row that is not the built-in one gets none"
