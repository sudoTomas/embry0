import pytest

from embry0.storage.repositories.agent_definitions import BUILTIN_SEED, AgentDefinitionsRepository
from embry0.workflows.qa.agent_seed import QA_AGENT_SEED, seed_qa_agent


def test_seed_definition_shape():
    assert QA_AGENT_SEED["model"] == "claude-sonnet-4-6"
    assert "Read" in QA_AGENT_SEED["tools"]
    assert "Bash" in QA_AGENT_SEED["tools"]
    assert "Write" not in QA_AGENT_SEED["tools"]  # QA never writes source
    assert "playwright" in QA_AGENT_SEED["mcp_servers"]
    assert QA_AGENT_SEED["mcp_servers"]["playwright"]["type"] == "stdio"


def test_qa_seed_in_sync_with_builtin_seed():
    """BUILTIN_SEED's qa entry must match QA_AGENT_SEED's standard fields.

    Catches drift between the source-of-truth seed (QA_AGENT_SEED in
    agent_seed.py) and the repo's BUILTIN_SEED used by reset(). They live
    in different files to avoid a circular import — this test is the seam
    that keeps them honest.
    """
    builtin = BUILTIN_SEED["qa"]
    for key in (
        "description",
        "model",
        "tools",
        "skills",
        "system_prompt",
        "execution_mode",
        "auth_mode",
        "mcp_servers",
    ):
        assert builtin[key] == QA_AGENT_SEED[key], f"BUILTIN_SEED.qa.{key} drifted from QA_AGENT_SEED"


@pytest.mark.requires_postgres
@pytest.mark.asyncio
async def test_seed_inserts_qa_agent(db_with_migrations):
    repo = AgentDefinitionsRepository(db_with_migrations)
    await seed_qa_agent(repo)
    row = await repo.get("qa")
    assert row is not None
    assert row["model"] == "claude-sonnet-4-6"
    assert row["is_builtin"] is True
    # mcp_servers persisted via repo.update() rather than direct SQL
    assert row["mcp_servers"]["playwright"]["command"] == "playwright-mcp"
    assert "--headless" in row["mcp_servers"]["playwright"]["args"]


@pytest.mark.requires_postgres
@pytest.mark.asyncio
async def test_seed_idempotent(db_with_migrations):
    repo = AgentDefinitionsRepository(db_with_migrations)
    await seed_qa_agent(repo)
    await seed_qa_agent(repo)
    row = await repo.get("qa")
    assert row is not None
    # Idempotency must preserve mcp_servers across re-seeds
    assert row["mcp_servers"]["playwright"]["command"] == "playwright-mcp"
