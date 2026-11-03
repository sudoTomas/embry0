"""Every write of ``agent_definitions.triage_may_add_skills`` checks it: create, update, reset and the seeders."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from embry0.storage.database import DatabasePool
from embry0.storage.repositories.agent_definitions import BUILTIN_SEED, AgentDefinitionsRepository

BAD = [
    "brainstorming",
    "Superpowers:Brainstorming",
    "superpowers:brain storming",
    "a" * 65 + ":x",
    7,
]


def _offline_repo() -> tuple[AgentDefinitionsRepository, MagicMock]:
    db = MagicMock()
    db.fetchrow = AsyncMock(return_value={"type": "x"})
    return AgentDefinitionsRepository(db), db


@pytest.mark.parametrize("bad", BAD)
async def test_create_refuses_a_name_that_is_not_source_skill(bad: Any) -> None:
    repo, db = _offline_repo()
    with pytest.raises(ValueError, match="triage_may_add_skills"):
        await repo.create("lantern", "d", "m", triage_may_add_skills=[bad])
    db.fetchrow.assert_not_awaited()


@pytest.mark.parametrize("bad", BAD)
async def test_update_refuses_a_name_that_is_not_source_skill(bad: Any) -> None:
    repo, db = _offline_repo()
    with pytest.raises(ValueError, match="triage_may_add_skills"):
        await repo.update("developer", triage_may_add_skills=[bad])
    db.fetchrow.assert_not_awaited()


async def test_update_refuses_too_many_names_and_stores_each_once() -> None:
    repo, db = _offline_repo()
    with pytest.raises(ValueError, match="at most"):
        await repo.update("developer", triage_may_add_skills=[f"sp:s{i}" for i in range(21)])
    await repo.update("developer", triage_may_add_skills=["sp:a", "sp:b", "sp:a"])
    assert ["sp:a", "sp:b"] in db.fetchrow.await_args.args, "a repeated name must be stored once"


async def test_reset_refuses_a_malformed_seed(monkeypatch: pytest.MonkeyPatch) -> None:
    repo, db = _offline_repo()
    monkeypatch.setitem(BUILTIN_SEED["developer"], "triage_may_add_skills", ["not a name"])
    with pytest.raises(ValueError, match="triage_may_add_skills"):
        await repo.reset("developer")
    db.fetchrow.assert_not_awaited()


@pytest.mark.parametrize("bad", ["sp:a", {"sp:a": True}, ("sp:a",)], ids=["string", "dict", "tuple"])
async def test_a_value_that_is_not_a_list_is_refused(bad: Any) -> None:
    repo, db = _offline_repo()
    with pytest.raises(ValueError, match="must be a list"):
        await repo.update("developer", triage_may_add_skills=bad)
    db.fetchrow.assert_not_awaited()


def test_every_seed_holds_a_well_formed_value() -> None:
    from embry0.agents.skills import checked_triage_may_add

    for agent_type, seed in BUILTIN_SEED.items():
        value = seed.get("triage_may_add_skills")
        assert value is not None and checked_triage_may_add(value) == value, f"{agent_type}: {value!r}"


def test_the_name_shape_is_the_skill_sources() -> None:
    """A source:skill name is what a skill source names its skills (embry0/skills/sync.py)."""
    from embry0.agents.skills import _CATALOG_SKILL_NAME_RE
    from embry0.skills.sync import _PLUGIN_NAME_RE, _SKILL_NAME_RE

    plugin, skill = _PLUGIN_NAME_RE.pattern.strip("^$"), _SKILL_NAME_RE.pattern.strip("^$")
    assert _CATALOG_SKILL_NAME_RE.pattern == f"^{plugin}:{skill}$", _CATALOG_SKILL_NAME_RE.pattern


@pytest.mark.requires_postgres
async def test_the_column_round_trips_and_reset_restores_the_seed(db_with_migrations: DatabasePool) -> None:
    repo = AgentDefinitionsRepository(db_with_migrations)
    created = await repo.create("lantern", "d", "m", skills=["sp:a"], triage_may_add_skills=["sp:b"])
    assert created["triage_may_add_skills"] == ["sp:b"], created
    assert (await repo.create("plain", "d", "m"))["triage_may_add_skills"] == [], "a row names none by default"
    updated = await repo.update("developer", triage_may_add_skills=[])
    assert updated["triage_may_add_skills"] == [], updated
    reset = await repo.reset("developer")
    assert reset["triage_may_add_skills"] == BUILTIN_SEED["developer"]["triage_may_add_skills"], reset


@pytest.mark.requires_postgres
async def test_the_seeder_writes_the_developers_value_when_it_seeds_the_developer(
    db_with_migrations: DatabasePool,
) -> None:
    from embry0.storage.seeds.agents_builtin import seed_missing_builtin_agents

    await db_with_migrations.execute("DELETE FROM agent_definitions WHERE type = 'developer'")
    repo = AgentDefinitionsRepository(db_with_migrations)
    await seed_missing_builtin_agents(repo)
    seeded = await repo.get("developer")
    assert (
        seeded is not None and seeded["triage_may_add_skills"] == BUILTIN_SEED["developer"]["triage_may_add_skills"]
    ), f"the seeder must write the developer's seed value: {seeded and seeded['triage_may_add_skills']}"
