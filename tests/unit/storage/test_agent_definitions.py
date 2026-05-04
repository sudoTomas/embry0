import pytest

from athanor.storage.database import DatabasePool
from athanor.storage.repositories.agent_definitions import (
    BUILTIN_SEED,
    AgentDefinitionsRepository,
)

pytestmark = pytest.mark.requires_postgres


@pytest.fixture
async def agent_repo(db_with_migrations: DatabasePool) -> AgentDefinitionsRepository:
    return AgentDefinitionsRepository(db_with_migrations)


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
        "review",
        description="Modified description",
        model="claude-haiku-4-5",
        system_prompt="Overridden prompt",
    )
    modified = await agent_repo.get("review")
    assert modified is not None
    assert modified["description"] == "Modified description"

    # Reset it
    restored = await agent_repo.reset("review")
    seed = BUILTIN_SEED["review"]
    assert restored["description"] == seed["description"]
    assert restored["model"] == seed["model"]
    assert restored["system_prompt"] == seed["system_prompt"]

    fetched = await agent_repo.get("review")
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


def test_builtin_seed_developer_model_is_current():
    """BUILTIN_SEED developer model must match migration 14's post-state.

    Migration 14 bumped the model from claude-opus-4-6 to claude-opus-4-7.
    reset() must restore to the same version, not downgrade.
    """
    assert BUILTIN_SEED["developer"]["model"] == "claude-opus-4-7", (
        "BUILTIN_SEED['developer']['model'] is out of sync with migration 14. "
        "Update agent_definitions.py when bumping the default model."
    )


@pytest.mark.asyncio
async def test_reset_developer_returns_current_model(agent_repo: AgentDefinitionsRepository):
    """reset('developer') must return claude-opus-4-7 after migration 14."""
    result = await agent_repo.reset("developer")
    assert result["model"] == "claude-opus-4-7"


def test_builtin_seed_skills_are_namespaced_superpowers():
    """All seeded skills must be namespaced (`<plugin>:<skill>` form).

    The agent runtime resolves bare skill names against the operator's local
    skills directory, which is brittle. Namespaced names load deterministically
    from the corresponding plugin (e.g. `superpowers:test-driven-development`).
    """
    for agent_type, cfg in BUILTIN_SEED.items():
        for skill in cfg.get("skills", []):
            assert ":" in skill, (
                f"BUILTIN_SEED['{agent_type}']['skills'] contains bare skill name "
                f"'{skill}' — must be namespaced as '<plugin>:<skill>'."
            )


def test_builtin_seed_skill_counts_match_design_2026_05_04():
    """Skill counts seeded into the four agents match the agreed design.

    Updates to skill counts ship as their own PR with this test updated. This
    catches accidental drift when the seed is re-keyed for unrelated reasons.

    See: docs/superpowers/specs/2026-05-04-expand-superpowers-skills-design.md
    """
    expected = {
        "triage":    {"writing-plans", "brainstorming"},
        "developer": {
            "subagent-driven-development",
            "verification-before-completion",
            "test-driven-development",
            "systematic-debugging",
            "executing-plans",
            "receiving-code-review",
        },
        "review":    {"requesting-code-review"},
    }
    for agent_type, want in expected.items():
        got = {
            s.split(":", 1)[1]
            for s in BUILTIN_SEED[agent_type]["skills"]
        }
        assert got == want, (
            f"BUILTIN_SEED['{agent_type}']['skills'] drifted from the 2026-05-04 design.\n"
            f"  expected: {sorted(want)}\n"
            f"  got:      {sorted(got)}"
        )
