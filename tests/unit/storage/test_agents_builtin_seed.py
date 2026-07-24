"""Non-code builtin agent seeds + startup insert-if-missing seeder (RAV-604)."""

import pytest

from embry0.storage.repositories.agent_definitions import BUILTIN_SEED
from embry0.storage.seeds.agents_builtin import seed_missing_builtin_agents

_NONCODE_TYPES = ("research", "analysis", "ops")


def test_noncode_agents_in_builtin_seed():
    for agent_type in _NONCODE_TYPES:
        seed = BUILTIN_SEED[agent_type]
        assert seed["model"], agent_type
        assert seed["system_prompt"].strip(), agent_type
        assert seed["tools"], agent_type
        assert seed["mcp_servers"] == {}


def test_research_and_analysis_are_read_only():
    """The read-only contract: no Write/Edit for research/analysis (owner decision)."""
    for agent_type in ("research", "analysis"):
        tools = BUILTIN_SEED[agent_type]["tools"]
        assert "Write" not in tools, agent_type
        assert "Edit" not in tools, agent_type


def test_ops_can_mutate_workspace():
    tools = BUILTIN_SEED["ops"]["tools"]
    assert "Write" in tools
    assert "Edit" in tools
    assert "Bash" in tools


def test_deliverable_contract_in_prompts():
    """finalize_output scrapes the last message — every prompt must say so."""
    for agent_type in _NONCODE_TYPES:
        assert "final message" in BUILTIN_SEED[agent_type]["system_prompt"].lower(), agent_type


class _FakeDb:
    def __init__(self):
        self.executed = []

    async def execute(self, sql, *params):
        self.executed.append((sql, params))


class _FakeRepo:
    def __init__(self, existing_types):
        self._existing = set(existing_types)
        self.created = []
        self._db = _FakeDb()

    async def get(self, agent_type):
        return {"type": agent_type} if agent_type in self._existing else None

    async def create(self, agent_type, **fields):
        self.created.append((agent_type, fields))
        self._existing.add(agent_type)
        return {"type": agent_type, **fields}


@pytest.mark.asyncio
async def test_seeder_inserts_only_missing_agents():
    # Everything but the non-code trio already exists (a pre-RAV-604 DB).
    repo = _FakeRepo(set(BUILTIN_SEED) - set(_NONCODE_TYPES))
    await seed_missing_builtin_agents(repo)  # type: ignore[arg-type]

    created_types = [t for t, _ in repo.created]
    assert sorted(created_types) == sorted(_NONCODE_TYPES)
    # Each insert is followed by the is_builtin flip.
    assert len(repo._db.executed) == len(_NONCODE_TYPES)
    for _sql, params in repo._db.executed:
        assert params[0] in _NONCODE_TYPES
    # Full seed payload threaded through, including mcp_servers.
    for agent_type, fields in repo.created:
        seed = BUILTIN_SEED[agent_type]
        assert fields["system_prompt"] == seed["system_prompt"]
        assert fields["tools"] == seed["tools"]
        assert fields["mcp_servers"] == seed["mcp_servers"]


@pytest.mark.asyncio
async def test_seeder_never_overwrites_existing_rows():
    repo = _FakeRepo(set(BUILTIN_SEED))
    await seed_missing_builtin_agents(repo)  # type: ignore[arg-type]
    assert repo.created == []
    assert repo._db.executed == []
