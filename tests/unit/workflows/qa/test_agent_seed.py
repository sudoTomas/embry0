import pytest

from embry0.storage.repositories.agent_definitions import BUILTIN_SEED, AgentDefinitionsRepository
from embry0.workflows.qa.agent_seed import QA_AGENT_SEED, SYSTEM_PROMPT, seed_qa_agent


def test_seed_definition_shape():
    assert QA_AGENT_SEED["model"] == "claude-sonnet-5"
    assert "Read" in QA_AGENT_SEED["tools"]
    assert "Bash" in QA_AGENT_SEED["tools"]
    assert "Write" not in QA_AGENT_SEED["tools"]  # QA never writes source
    assert "playwright" in QA_AGENT_SEED["mcp_servers"]
    assert QA_AGENT_SEED["mcp_servers"]["playwright"]["type"] == "stdio"


def test_qa_seed_in_sync_with_builtin_seed():
    """BUILTIN_SEED's qa entry must equal QA_AGENT_SEED without sharing state.

    Since #230 the builtin entry IS a deepcopy of the source-of-truth seed,
    so equality is by construction; what this test now guards is the
    isolation — mutating one (e.g. via reset()) must never bleed into the
    other through shared lists/dicts.
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
    # Deepcopy isolation: no shared mutable objects.
    assert builtin is not QA_AGENT_SEED
    assert builtin["tools"] is not QA_AGENT_SEED["tools"]
    assert builtin["skills"] is not QA_AGENT_SEED["skills"]
    assert builtin["mcp_servers"] is not QA_AGENT_SEED["mcp_servers"]
    assert builtin["mcp_servers"]["playwright"] is not QA_AGENT_SEED["mcp_servers"]["playwright"]
    assert builtin["mcp_servers"]["playwright"]["args"] is not QA_AGENT_SEED["mcp_servers"]["playwright"]["args"]


@pytest.mark.requires_postgres
@pytest.mark.asyncio
async def test_seed_inserts_qa_agent(db_with_migrations):
    repo = AgentDefinitionsRepository(db_with_migrations)
    await seed_qa_agent(repo)
    row = await repo.get("qa")
    assert row is not None
    assert row["model"] == "claude-sonnet-5"
    assert row["is_builtin"] is True
    # mcp_servers persisted by repo.create(), not direct SQL
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


def test_system_prompt_has_stuck_protocol():
    """EMB-37: the soft time-box guidance is replaced by a mandatory stuck
    protocol with a concrete self-abort command."""
    assert "Stuck protocol" in SYSTEM_PROMPT
    assert "python -m embry0.sandbox.qa_abort" in SYSTEM_PROMPT
    # result.json creation guidance points at Bash/Edit, not the unlisted Write tool
    assert "via a Bash heredoc or Edit" in SYSTEM_PROMPT
