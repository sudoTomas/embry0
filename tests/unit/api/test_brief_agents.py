"""The brief writer's prompts are built-in agent definitions (#166), end to end on a real database.

The seeder stores each shipped prompt byte for byte; an operator reads and
changes it as any built-in agent, through the agents API that the MCP tools
``agent_get``, ``agent_update`` and ``agent_reset`` call; the brief loops'
readers take the change on their next tick; a reset puts the shipped prompt
back. ``tests/unit/api/test_background_models_settings.py`` runs the real
lifespan's writers.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient

from embry0.api.app import create_app
from embry0.config import Embry0Config
from embry0.services import job_briefs, proposal_briefs
from embry0.storage.database import DatabasePool
from embry0.storage.repositories.agent_definitions import BUILTIN_SEED, AgentDefinitionsRepository
from embry0.storage.seeds.agents_builtin import seed_missing_builtin_agents

pytestmark = pytest.mark.requires_postgres

#: Each brief loop's agent definition and the voice it ships with, read so this file collects on a tree without it.
VOICES = {
    "proposal-brief": getattr(proposal_briefs, "VOICE", None),
    "job-brief": getattr(job_briefs, "VOICE", None),
}


class Store:
    """The settings store with nothing written: every brief's model is the section's default."""

    async def snapshot(self) -> Any:
        from embry0.settings.sections import CATALOG
        from embry0.settings.store import build_snapshot

        return build_snapshot(CATALOG, [], token=(), log=False)


@pytest.fixture
async def client(db_with_migrations: DatabasePool) -> AsyncIterator[AsyncClient]:
    app = create_app(Embry0Config(_env_file=None, auth_dev_mode=True, webhook_dev_mode=True))
    app.state.db = db_with_migrations
    app.state.agent_defs_repo = AgentDefinitionsRepository(db_with_migrations)
    headers = {"X-Requested-With": "XMLHttpRequest"}
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test", headers=headers) as c:
        yield c


#: Each brief loop's settings reader, as production builds it.
READERS = {"proposal-brief": "proposal_briefs.brief_settings", "job-brief": "job_briefs.job_brief_settings"}


async def _voice(agents: Any, agent_type: str) -> str:
    """The voice the brief loop of ``agent_type`` asks with at its next tick, through its production reader."""
    module, name = READERS[agent_type].split(".")
    reader = getattr({"proposal_briefs": proposal_briefs, "job_briefs": job_briefs}[module], name, None)
    assert reader is not None, "each brief loop reads its voice through its settings reader (#166)"
    return str((await reader(Store(), agents)()).voice)


async def test_the_brief_voices_are_seeded_edited_and_reset_as_built_in_agents(
    client: AsyncClient, db_with_migrations: DatabasePool
) -> None:
    assert None not in VOICES.values(), "a brief's prompt is a voice, the agent definition's, then the code's format"
    assert set(VOICES) <= set(BUILTIN_SEED), "the brief writer's voices are seeded as built-in agents"
    agents = AgentDefinitionsRepository(db_with_migrations)
    await seed_missing_builtin_agents(agents)

    for agent_type, shipped in VOICES.items():
        row = (await client.get(f"/api/v1/agents/{agent_type}")).json()
        stored = (row["system_prompt"], row["is_builtin"], row["model"], row.get("runs_in_jobs"))
        assert stored == (shipped, True, "", False), (
            f"{agent_type} is stored byte for byte as shipped, a built-in naming no model, that no job runs"
        )
        assert await _voice(agents, agent_type) == shipped, f"the {agent_type} loop asks with the stored voice"

        edited = f"Write every brief in plain German. {shipped}"
        resp = await client.put(f"/api/v1/agents/{agent_type}", json={"system_prompt": edited})
        assert resp.status_code == 200, f"an operator edits {agent_type} as any agent (agent_update): {resp.text}"
        assert await _voice(agents, agent_type) == edited, f"the {agent_type} loop's next tick asks with the edit"

        refused = await client.put(
            f"/api/v1/agents/{agent_type}", json={"system_prompt": 'Antworte auf Deutsch.\n- "title": ein Name'}
        )
        assert refused.status_code == 422, f"a voice may not define one of the answer's keys: {refused.text}"
        assert "Deutsch" not in refused.text, "the refusal never repeats the voice"
        assert await _voice(agents, agent_type) == edited, "and nothing was stored"
        prose = "Write for engineers who read JSON logs all day: keep it short and avoid jargon."
        resp = await client.put(f"/api/v1/agents/{agent_type}", json={"system_prompt": prose})
        assert resp.status_code == 200, f"a voice in ordinary prose may name JSON: {resp.text}"
        assert await _voice(agents, agent_type) == prose

        resp = await client.post(f"/api/v1/agents/{agent_type}/reset")
        assert resp.status_code == 200, f"agent_reset restores {agent_type}: {resp.text}"
        assert (await _voice(agents, agent_type), resp.json()["runs_in_jobs"]) == (shipped, False), (
            "the reset puts the shipped voice back, and the row still runs in no job"
        )

    listed = {a["type"] for a in (await client.get("/api/v1/agents")).json()}
    assert set(VOICES) <= listed, "the Agents page lists both, where an operator finds them"
    resp = await client.delete("/api/v1/agents/proposal-brief")
    assert resp.status_code == 403, f"a built-in is never deleted: {resp.status_code}"
    resp = await client.put("/api/v1/agents/developer", json={"system_prompt": 'Answer as JSON: "verdict".'})
    assert resp.status_code == 200, "a job's agent may say anything in its prompt: only a voice is held to it"


async def test_a_definition_no_job_runs_is_no_step_and_no_character(db_with_migrations: DatabasePool) -> None:
    """``runs_in_jobs`` (#166), a column on the row, not a list of names: a template naming a brief voice is
    refused at save time and at plan time, and the guild draws no character for it."""
    from types import SimpleNamespace

    from embry0.api.v1.pipeline_templates import _known_agent_types as saved_types
    from embry0.storage.repositories.progression import ProgressionRepository
    from embry0.workflows._validation import validate_graph_definition
    from embry0.workflows.issue_to_pr.plan_route import _known_agent_types as planned_types

    agents = AgentDefinitionsRepository(db_with_migrations)
    await seed_missing_builtin_agents(agents)
    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(agent_defs_repo=agents)))
    at_save = await saved_types(request)  # type: ignore[arg-type]
    at_plan = await planned_types({"configurable": {"db": db_with_migrations}})  # type: ignore[arg-type]
    characters = set(await ProgressionRepository(db_with_migrations).agent_types())
    for name, found in (("a template's save", at_save), ("a job's plan", at_plan), ("the guild", characters)):
        assert found is not None and "developer" in found, f"{name} reads the definitions"
        assert not set(VOICES) & set(found), f"{name} leaves out the definitions no job runs: {sorted(found)}"
    graph = {
        "nodes": [{"node_id": "t", "agent_type": "triage"}, {"node_id": "b", "agent_type": "proposal-brief"}],
        "edges": [{"edge_id": "t-b", "source": "t", "target": "b", "edge_type": "flow"}],
    }
    for types in (at_save, at_plan, None):
        assert any("unknown agent_type 'proposal-brief'" in p for p in validate_graph_definition("x", graph, types))


async def test_an_agent_the_deployment_made_under_a_voice_s_name_stays_its_own(
    client: AsyncClient, db_with_migrations: DatabasePool
) -> None:
    """A deployment that made its own ``proposal-brief`` agent before the voices were seeded keeps it, as a job
    agent: the seeder never overwrites it, a reset never turns it into the voice, and the brief loop never reads
    its prompt as the voice: it writes with the shipped one."""
    from embry0.storage.repositories.pipeline_templates import PipelineTemplatesRepository
    from embry0.storage.repositories.progression import ProgressionRepository
    from embry0.workflows._validation import validate_graph_definition
    from embry0.workflows.issue_to_pr.plan_route import _known_agent_types as planned_types

    agents = AgentDefinitionsRepository(db_with_migrations)
    own = "You are a careful developer. Use tools to fix the bug."
    await agents.create(
        agent_type="proposal-brief",
        description="an agent of the deployment's own",
        model="claude-sonnet-5",
        tools=["Read"],
        skills=[],
        system_prompt=own,
        execution_mode=None,
        auth_mode=None,
        mcp_servers={},
        display_name="Mine",
        avatar="apprentice",
        color="sky",
        tagline="",
    )
    graph = {"nodes": [{"node_id": "write", "agent_type": "proposal-brief"}], "edges": []}
    await PipelineTemplatesRepository(db_with_migrations).create(name="brief-step", graph_definition=graph)
    await seed_missing_builtin_agents(agents)

    def kept(row: dict[str, Any] | None) -> tuple[Any, ...]:
        return tuple((row or {}).get(k) for k in ("system_prompt", "is_builtin", "runs_in_jobs", "model"))

    assert kept(await agents.get("proposal-brief")) == (own, False, True, "claude-sonnet-5"), "the seeder skips it"
    assert await _voice(agents, "proposal-brief") == VOICES["proposal-brief"], (
        "the brief loop never reads a job agent's prompt as its voice: it writes with the shipped one"
    )
    assert await _voice(agents, "job-brief") == VOICES["job-brief"], "the other voice is seeded as usual"

    resp = await client.post("/api/v1/agents/proposal-brief/reset")
    assert resp.status_code == 404, f"a reset is for a built-in only, never for the deployment's own: {resp.text}"
    assert kept(await agents.get("proposal-brief")) == (own, False, True, "claude-sonnet-5"), "nothing changed"
    resp = await client.put("/api/v1/agents/proposal-brief", json={"system_prompt": f'{own}\n- "title": the fix'})
    assert resp.status_code == 200, f"a job agent's prompt is not held to a voice's check: {resp.text}"

    types = await planned_types({"configurable": {"db": db_with_migrations}})  # type: ignore[arg-type]
    assert validate_graph_definition("brief-step", graph, types) == [], "its template still plans"
    assert "proposal-brief" in await ProgressionRepository(db_with_migrations).agent_types(), "and it is in the guild"
