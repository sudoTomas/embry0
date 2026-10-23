import pytest

from embry0.storage.database import DatabasePool
from embry0.storage.repositories.agent_definitions import (
    BUILTIN_SEED,
    AgentDefinitionsRepository,
)

pytestmark = pytest.mark.requires_postgres


@pytest.fixture
async def agent_repo(db_with_migrations: DatabasePool) -> AgentDefinitionsRepository:
    # Migrations seed triage/developer/review. Everything else in BUILTIN_SEED
    # arrives at app startup (embry0/api/app.py): qa, onboarding and
    # pr-reviewer have their own insert-if-missing seeders, and
    # seed_missing_builtin_agents inserts the rest — the non-code agents
    # (research/analysis/ops, RAV-604). Mirror all four here so
    # BUILTIN_SEED ⊆ list_all() holds; omitting the last one is
    # why this fixture went stale when the non-code agents were added.
    from embry0.storage.seeds.agents_builtin import seed_missing_builtin_agents
    from embry0.workflows.onboard.agent_seed import seed_onboarding_agent
    from embry0.workflows.pr_review.agent_seed import seed_pr_reviewer_agent
    from embry0.workflows.qa.agent_seed import seed_qa_agent

    repo = AgentDefinitionsRepository(db_with_migrations)
    await seed_qa_agent(repo)
    await seed_onboarding_agent(repo)
    await seed_pr_reviewer_agent(repo)
    await seed_missing_builtin_agents(repo)
    return repo


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

    Migration 14 bumped the model from claude-opus-5 to claude-opus-5.
    reset() must restore to the same version, not downgrade.
    """
    assert BUILTIN_SEED["developer"]["model"] == "claude-opus-5", (
        "BUILTIN_SEED['developer']['model'] is out of sync with migration 14. "
        "Update agent_definitions.py when bumping the default model."
    )


@pytest.mark.asyncio
async def test_reset_developer_returns_current_model(agent_repo: AgentDefinitionsRepository):
    """reset('developer') must return claude-opus-5 after migration 14."""
    result = await agent_repo.reset("developer")
    assert result["model"] == "claude-opus-5"


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

    The 2026-05-04 design note that agreed these counts was never committed
    to this repository.
    """
    expected = {
        "triage": {"writing-plans", "brainstorming"},
        "developer": {
            "subagent-driven-development",
            "verification-before-completion",
            "test-driven-development",
            "systematic-debugging",
            "executing-plans",
            "receiving-code-review",
        },
        "review": {"requesting-code-review"},
    }
    for agent_type, want in expected.items():
        got = {s.split(":", 1)[1] for s in BUILTIN_SEED[agent_type]["skills"]}
        assert got == want, (
            f"BUILTIN_SEED['{agent_type}']['skills'] drifted from the 2026-05-04 design.\n"
            f"  expected: {sorted(want)}\n"
            f"  got:      {sorted(got)}"
        )


async def test_create_stores_identity_and_defaults(db_with_migrations) -> None:
    repo = AgentDefinitionsRepository(db_with_migrations)
    row = await repo.create(agent_type="cartographer", description="maps", model="claude-sonnet-5")
    assert (row["display_name"], row["avatar"], row["color"], row["tagline"]) == ("", "golem", "slate", "")
    row = await repo.create(
        agent_type="lantern",
        description="lights",
        model="claude-sonnet-5",
        display_name="Lantern",
        avatar="bard",
        color="sky",
        tagline="Sings the changelog.",
    )
    assert (row["display_name"], row["avatar"], row["color"]) == ("Lantern", "bard", "sky")


async def test_update_accepts_identity_fields(db_with_migrations) -> None:
    repo = AgentDefinitionsRepository(db_with_migrations)
    await repo.create(agent_type="lantern", description="lights", model="claude-sonnet-5")
    row = await repo.update("lantern", display_name="Lantern", avatar="rogue", color="rose", tagline="t")
    assert (row["display_name"], row["avatar"], row["color"], row["tagline"]) == ("Lantern", "rogue", "rose", "t")


async def test_reset_restores_builtin_identity(db_with_migrations) -> None:
    repo = AgentDefinitionsRepository(db_with_migrations)
    await repo.update("developer", display_name="Renamed", avatar="golem", color="slate", tagline="x")
    row = await repo.reset("developer")
    assert (row["display_name"], row["avatar"], row["color"]) == ("Developer", "smith", "amber"), (
        "reset must restore the seeded identity with the rest of the row"
    )


async def test_every_builtin_row_carries_its_identity(agent_repo: AgentDefinitionsRepository) -> None:
    """All eighteen builtin rows (nine core agents, nine specialists) match BUILTIN_IDENTITY
    after the startup seeders run, whether migration 101 wrote the identity or a seeder inserted the row."""
    from embry0.agents.identity import BUILTIN_IDENTITY

    rows = {r["type"]: r for r in await agent_repo.list_all()}
    mismatched = {
        agent_type: {k: rows.get(agent_type, {}).get(k) for k in ident}
        for agent_type, ident in BUILTIN_IDENTITY.items()
        if {k: rows.get(agent_type, {}).get(k) for k in ident} != ident
    }
    assert len(BUILTIN_IDENTITY) == 18, "nine core agents and nine specialists"
    assert mismatched == {}, f"builtin rows whose identity differs from BUILTIN_IDENTITY: {mismatched}"


def _insert_if_missing_seeders():
    from embry0.workflows.onboard.agent_seed import seed_onboarding_agent
    from embry0.workflows.pr_review.agent_seed import seed_pr_reviewer_agent
    from embry0.workflows.qa.agent_seed import seed_qa_agent

    return [
        pytest.param("qa", seed_qa_agent, id="qa"),
        pytest.param("onboarding", seed_onboarding_agent, id="onboarding"),
        pytest.param("pr-reviewer", seed_pr_reviewer_agent, id="pr-reviewer"),
    ]


@pytest.mark.parametrize(("agent_type", "seeder"), _insert_if_missing_seeders())
async def test_reseeding_keeps_identity_and_skill_edits(db_with_migrations, agent_type, seeder) -> None:
    """A seeder leaves an existing row alone: an operator's identity edits and added skills survive a
    reboot like every other field (test_builtin_agent_edits_stick.py); POST /agents/{type}/reset is
    the explicit restore path for all of them."""
    repo = AgentDefinitionsRepository(db_with_migrations)
    await seeder(repo)
    seeded_skills = list(BUILTIN_SEED[agent_type]["skills"])
    added = [*seeded_skills, "superpowers:brainstorming"]
    await repo.update(
        agent_type, display_name="Renamed", avatar="rogue", color="rose", description="edited", skills=added
    )
    await seeder(repo)
    row = await repo.get(agent_type)
    assert row is not None
    assert (row["display_name"], row["avatar"], row["color"]) == ("Renamed", "rogue", "rose"), (
        f"re-running the {agent_type} seeder reverted the operator's identity edits"
    )
    assert row["skills"] == added, f"re-running the {agent_type} seeder undid an added skill: {row['skills']}"
    assert row["description"] == "edited", "the seeder must leave an existing row's description alone"
    reset = await repo.reset(agent_type)
    assert reset["skills"] == seeded_skills, "reset returns the skills to the seed"
