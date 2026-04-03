import os

import asyncpg
import pytest

from legion.storage.database import DatabasePool
from legion.storage.migrations.runner import run_migrations
from legion.storage.repositories.agent_definitions import (
    BUILTIN_SEED,
    AgentDefinitionsRepository,
)


@pytest.fixture
async def agent_repo(pg_pool: asyncpg.Pool) -> AgentDefinitionsRepository:
    url = os.environ.get("TEST_DATABASE_URL", "postgresql://legion:legion@localhost:5432/legion_test")
    db = DatabasePool(url)
    await db.connect()
    await run_migrations(db)
    yield AgentDefinitionsRepository(db)
    await db.close()


@pytest.mark.asyncio
async def test_list_all_returns_builtin_agents(agent_repo: AgentDefinitionsRepository):
    agents = await agent_repo.list_all()
    types = {a["type"] for a in agents}
    assert set(BUILTIN_SEED.keys()).issubset(types)


@pytest.mark.asyncio
async def test_get_returns_specific_agent(agent_repo: AgentDefinitionsRepository):
    agent = await agent_repo.get("triage")
    assert agent is not None
    assert agent["type"] == "triage"
    assert agent["is_builtin"] is True
    assert agent["model"] == BUILTIN_SEED["triage"]["model"]


@pytest.mark.asyncio
async def test_get_nonexistent_returns_none(agent_repo: AgentDefinitionsRepository):
    result = await agent_repo.get("nonexistent-agent-xyz")
    assert result is None


@pytest.mark.asyncio
async def test_create_custom_agent(agent_repo: AgentDefinitionsRepository):
    agent = await agent_repo.create(
        agent_type="custom-bot",
        description="A custom agent for testing",
        model="claude-haiku-4-5",
        tools=["Read", "Write"],
        skills=[],
        system_prompt="You are a test agent.",
    )
    assert agent["type"] == "custom-bot"
    assert agent["description"] == "A custom agent for testing"
    assert agent["model"] == "claude-haiku-4-5"
    assert agent["is_builtin"] is False
    assert agent["system_prompt"] == "You are a test agent."

    fetched = await agent_repo.get("custom-bot")
    assert fetched is not None
    assert fetched["type"] == "custom-bot"


@pytest.mark.asyncio
async def test_create_duplicate_fails(agent_repo: AgentDefinitionsRepository):
    await agent_repo.create(
        agent_type="dup-agent",
        description="First",
        model="claude-haiku-4-5",
    )
    with pytest.raises(Exception):
        await agent_repo.create(
            agent_type="dup-agent",
            description="Second",
            model="claude-haiku-4-5",
        )


@pytest.mark.asyncio
async def test_update_agent(agent_repo: AgentDefinitionsRepository):
    await agent_repo.create(
        agent_type="update-me",
        description="Original description",
        model="claude-haiku-4-5",
    )
    updated = await agent_repo.update(
        "update-me",
        description="Updated description",
        model="claude-sonnet-4-6",
        tools=["Read", "Grep"],
    )
    assert updated["description"] == "Updated description"
    assert updated["model"] == "claude-sonnet-4-6"

    fetched = await agent_repo.get("update-me")
    assert fetched is not None
    assert fetched["description"] == "Updated description"
    assert fetched["model"] == "claude-sonnet-4-6"


@pytest.mark.asyncio
async def test_delete_custom_agent(agent_repo: AgentDefinitionsRepository):
    await agent_repo.create(
        agent_type="delete-me",
        description="Temporary agent",
        model="claude-haiku-4-5",
    )
    await agent_repo.delete("delete-me")
    assert await agent_repo.get("delete-me") is None


@pytest.mark.asyncio
async def test_delete_builtin_raises_value_error(agent_repo: AgentDefinitionsRepository):
    with pytest.raises(ValueError, match="Cannot delete built-in agent 'developer'"):
        await agent_repo.delete("developer")


@pytest.mark.asyncio
async def test_reset_builtin_restores_seed_values(agent_repo: AgentDefinitionsRepository):
    # First modify the built-in agent
    await agent_repo.update(
        "validator",
        description="Modified description",
        model="claude-haiku-4-5",
        system_prompt="Overridden prompt",
    )
    modified = await agent_repo.get("validator")
    assert modified is not None
    assert modified["description"] == "Modified description"

    # Reset it
    restored = await agent_repo.reset("validator")
    seed = BUILTIN_SEED["validator"]
    assert restored["description"] == seed["description"]
    assert restored["model"] == seed["model"]
    assert restored["system_prompt"] == seed["system_prompt"]

    fetched = await agent_repo.get("validator")
    assert fetched is not None
    assert fetched["description"] == seed["description"]


@pytest.mark.asyncio
async def test_reset_custom_raises_value_error(agent_repo: AgentDefinitionsRepository):
    await agent_repo.create(
        agent_type="custom-no-reset",
        description="Custom agent",
        model="claude-haiku-4-5",
    )
    with pytest.raises(ValueError, match="'custom-no-reset' is not a built-in agent"):
        await agent_repo.reset("custom-no-reset")
