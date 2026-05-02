import pytest

from athanor.storage.repositories.agent_definitions import AgentDefinitionsRepository
from athanor.workflows.qa.agent_seed import QA_AGENT_SEED, seed_qa_agent


def test_seed_definition_shape():
    assert QA_AGENT_SEED["model"] == "claude-sonnet-4-6"
    assert "Read" in QA_AGENT_SEED["tools"]
    assert "Bash" in QA_AGENT_SEED["tools"]
    assert "Write" not in QA_AGENT_SEED["tools"]  # QA never writes source
    assert "playwright" in QA_AGENT_SEED["mcp_servers"]
    assert QA_AGENT_SEED["mcp_servers"]["playwright"]["type"] == "stdio"


@pytest.mark.requires_postgres
@pytest.mark.asyncio
async def test_seed_inserts_qa_agent(db_with_migrations):
    repo = AgentDefinitionsRepository(db_with_migrations)
    await seed_qa_agent(repo)
    row = await repo.get("qa")
    assert row is not None
    assert row["model"] == "claude-sonnet-4-6"
    assert row["is_builtin"] is True


@pytest.mark.requires_postgres
@pytest.mark.asyncio
async def test_seed_idempotent(db_with_migrations):
    repo = AgentDefinitionsRepository(db_with_migrations)
    await seed_qa_agent(repo)
    await seed_qa_agent(repo)
    row = await repo.get("qa")
    assert row is not None
