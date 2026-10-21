"""An edit to a built-in agent survives the next start; reset is the way back.

The qa, onboarding and pr-reviewer seeders rewrote their rows at every
orchestrator start, so an edit made on the Agents page or with agent_update
lasted only until the next restart, while the other built-in agents kept
theirs. All of them now seed insert-if-missing.
"""

from __future__ import annotations

import pytest

from embry0.storage.repositories.agent_definitions import BUILTIN_SEED, AgentDefinitionsRepository
from embry0.storage.seeds.agents_builtin import seed_missing_builtin_agents
from embry0.workflows.onboard.agent_seed import seed_onboarding_agent
from embry0.workflows.pr_review.agent_seed import seed_pr_reviewer_agent
from embry0.workflows.qa.agent_seed import seed_qa_agent

pytestmark = pytest.mark.requires_postgres

_SEEDED_FIELDS = (
    "description",
    "model",
    "tools",
    "skills",
    "system_prompt",
    "execution_mode",
    "auth_mode",
    "mcp_servers",
)


async def _start(repo: AgentDefinitionsRepository) -> None:
    """The agent seeding embry0/api/app.py runs at every orchestrator start, in its order."""
    await seed_qa_agent(repo)
    await seed_onboarding_agent(repo)
    await seed_pr_reviewer_agent(repo)
    await seed_missing_builtin_agents(repo)


def _seeded(row: dict) -> dict:
    return {field: row[field] for field in _SEEDED_FIELDS}


@pytest.mark.parametrize("agent_type", ["qa", "onboarding", "pr-reviewer", "research"])
@pytest.mark.asyncio
async def test_an_edit_to_a_builtin_agent_survives_a_restart(db_with_migrations, agent_type: str) -> None:
    repo = AgentDefinitionsRepository(db_with_migrations)
    await _start(repo)
    await repo.update(agent_type, model="claude-haiku-4-5", system_prompt="edited by hand")

    await _start(repo)

    row = await repo.get(agent_type)
    assert row is not None and row["is_builtin"] is True
    kept = (row["model"], row["system_prompt"][:40])
    assert kept == ("claude-haiku-4-5", "edited by hand"), f"a restart overwrote the {agent_type} edit: {kept}"


@pytest.mark.parametrize("agent_type", ["qa", "onboarding", "pr-reviewer"])
@pytest.mark.asyncio
async def test_a_first_start_writes_the_whole_seed(db_with_migrations, agent_type: str) -> None:
    repo = AgentDefinitionsRepository(db_with_migrations)
    await _start(repo)
    row = await repo.get(agent_type)
    assert row is not None and row["is_builtin"] is True
    assert _seeded(row) == {field: BUILTIN_SEED[agent_type][field] for field in _SEEDED_FIELDS}


@pytest.mark.parametrize("agent_type", ["qa", "onboarding", "pr-reviewer"])
@pytest.mark.asyncio
async def test_reset_is_the_way_back_to_the_seed(db_with_migrations, agent_type: str) -> None:
    repo = AgentDefinitionsRepository(db_with_migrations)
    await _start(repo)
    await repo.update(agent_type, model="claude-haiku-4-5", tools=["Read"], mcp_servers={})

    await repo.reset(agent_type)

    row = await repo.get(agent_type)
    assert row is not None
    assert _seeded(row) == {field: BUILTIN_SEED[agent_type][field] for field in _SEEDED_FIELDS}
