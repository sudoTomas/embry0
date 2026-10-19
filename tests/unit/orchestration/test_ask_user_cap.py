"""Test that the ask-user cap is bumped to 15 when the brainstorming skill is loaded."""

from unittest.mock import AsyncMock, patch

import pytest
from langgraph.types import Command


@pytest.mark.asyncio
async def test_developer_with_brainstorming_skill_uses_cap_15():
    """When developer's agent_skills includes superpowers:brainstorming,
    the ask-user cap is 15 rounds (not 5)."""
    from embry0.workflows.issue_to_pr.nodes import developer_node

    # State: developer has 14 prior rounds + brainstorming skill loaded
    state = {
        "job_id": "JOB",
        "issue_id": "iss-x",
        "repo": "owner/repo",
        "task": "design something",
        "sandbox_container_id": "C",
        "agent_outputs": [],
        "errors": [],
        "agent_question_rounds": 14,
        "pipeline_config": {
            "agent_skills": {"developer": ["superpowers:brainstorming"]},
            "budget_usd": 50.0,
        },
    }

    # Stub run_agent_node to return a result with one new ask_user event
    fake_events = [{"type": "agent_ask_user", "question": "Q15?", "category": "design", "options": []}]
    fake_result = {
        "agent_outputs": [{"agent_type": "developer", "is_error": False, "output": "..."}],
        "events": fake_events,
        "total_cost_usd": 0.10,
    }

    async def _run(*args, **kwargs):
        cb = kwargs.get("on_event")
        if cb:
            for ev in fake_events:
                cb(ev)
        return fake_result

    with (
        patch("embry0.workflows.issue_to_pr.nodes.run_agent_node", new=AsyncMock(side_effect=_run)),
        patch("embry0.workflows.issue_to_pr.nodes.get_stream_writer", return_value=lambda _: None),
    ):
        result = await developer_node(state, {"configurable": {"agent_runner": object(), "credentials": {}}})

    # Should NOT be exhausted at round 15 — brainstorming cap is 15
    # (without the bump it would have exhausted at round 5)
    assert isinstance(result, Command)
    assert result.goto != "max_retries"
    update = result.update or {}
    assert not update.get("agent_questions_exhausted")
    assert update.get("agent_question_rounds") == 15


@pytest.mark.asyncio
async def test_developer_without_brainstorming_skill_uses_default_cap():
    """The default cap still applies when brainstorming isn't loaded."""
    from embry0.orchestration.nodes.agent import DEFAULT_ASK_USER_CAP
    from embry0.workflows.issue_to_pr.nodes import developer_node

    state = {
        "job_id": "JOB",
        "issue_id": "iss-x",
        "repo": "owner/repo",
        "task": "fix something",
        "sandbox_container_id": "C",
        "agent_outputs": [],
        "errors": [],
        "agent_question_rounds": DEFAULT_ASK_USER_CAP,
        "pipeline_config": {
            "agent_skills": {"developer": ["superpowers:writing-plans"]},
            "budget_usd": 50.0,
        },
    }

    fake_events = [{"type": "agent_ask_user", "question": "Q6?", "category": "general", "options": []}]
    fake_result = {
        "agent_outputs": [{"agent_type": "developer", "is_error": False, "output": "..."}],
        "events": fake_events,
        "total_cost_usd": 0.05,
    }

    async def _run(*args, **kwargs):
        cb = kwargs.get("on_event")
        if cb:
            for ev in fake_events:
                cb(ev)
        return fake_result

    with (
        patch("embry0.workflows.issue_to_pr.nodes.run_agent_node", new=AsyncMock(side_effect=_run)),
        patch("embry0.workflows.issue_to_pr.nodes.get_stream_writer", return_value=lambda _: None),
    ):
        result = await developer_node(state, {"configurable": {"agent_runner": object(), "credentials": {}}})

    assert isinstance(result, Command)
    update = result.update or {}
    assert update.get("agent_questions_exhausted") is True


def _brainstorm_cap_state(pipeline_config: dict) -> dict:
    return {
        "job_id": "JOB",
        "issue_id": "iss-x",
        "repo": "owner/repo",
        "task": "design something",
        "sandbox_container_id": "C",
        "agent_outputs": [],
        "errors": [],
        "agent_question_rounds": 14,
        "pipeline_config": {**pipeline_config, "budget_usd": 50.0},
    }


async def _run_developer_with_one_question(state: dict, config: dict):
    from embry0.workflows.issue_to_pr.nodes import developer_node

    fake_events = [{"type": "agent_ask_user", "question": "Q15?", "category": "design", "options": []}]
    fake_result = {
        "agent_outputs": [{"agent_type": "developer", "is_error": False, "output": "..."}],
        "events": fake_events,
        "total_cost_usd": 0.10,
    }

    async def _run(*args, **kwargs):
        cb = kwargs.get("on_event")
        if cb:
            for ev in fake_events:
                cb(ev)
        return fake_result

    with (
        patch("embry0.workflows.issue_to_pr.nodes.run_agent_node", new=AsyncMock(side_effect=_run)),
        patch("embry0.workflows.issue_to_pr.nodes.get_stream_writer", return_value=lambda _: None),
    ):
        return await developer_node(state, config)


@pytest.mark.asyncio
async def test_a_triage_skills_delta_adding_brainstorming_raises_the_cap():
    """Triage now emits {"add": [...]}; the cap reads the effective list, not the raw override."""
    state = _brainstorm_cap_state({"agent_skills": {"developer": {"add": ["superpowers:brainstorming"]}}})
    result = await _run_developer_with_one_question(
        state, {"configurable": {"agent_runner": object(), "credentials": {}}}
    )
    update = result.update or {}
    assert not update.get("agent_questions_exhausted"), "a brainstorming delta must lift the cap to 15"
    assert update.get("agent_question_rounds") == 15


@pytest.mark.asyncio
async def test_brainstorming_on_the_developer_definition_raises_the_cap():
    """A row that carries brainstorming gets the higher cap with no triage override at all."""
    from unittest.mock import MagicMock

    defs = MagicMock()
    defs.get = AsyncMock(
        return_value={
            "model": "claude-sonnet-5",
            "tools": [],
            "skills": ["superpowers:brainstorming"],
            "system_prompt": "",
        }
    )
    state = _brainstorm_cap_state({})
    with patch("embry0.storage.repositories.agent_definitions.AgentDefinitionsRepository", return_value=defs):
        result = await _run_developer_with_one_question(
            state, {"configurable": {"agent_runner": object(), "credentials": {}, "db": MagicMock()}}
        )
    defs.get.assert_awaited_with("developer")
    update = result.update or {}
    assert not update.get("agent_questions_exhausted"), "the definition's brainstorming skill must lift the cap"
    assert update.get("agent_question_rounds") == 15


@pytest.mark.asyncio
async def test_a_template_node_adding_brainstorming_raises_the_cap():
    """The cap reads the effective list the agent ran with: definition, template node delta, triage delta."""
    from embry0.workflows.issue_to_pr.route_plan import build_route_plan

    graph = {
        "nodes": [
            {"node_id": "t", "agent_type": "triage"},
            {"node_id": "d", "agent_type": "developer", "config": {"skills": {"add": ["superpowers:brainstorming"]}}},
        ],
        "edges": [{"edge_id": "e1", "source": "t", "target": "d", "edge_type": "flow"}],
    }
    state = {**_brainstorm_cap_state({}), "route_plan": build_route_plan(graph), "route_cursor": 0}
    result = await _run_developer_with_one_question(
        state, {"configurable": {"agent_runner": object(), "credentials": {}}}
    )
    update = result.update or {}
    assert not update.get("agent_questions_exhausted"), "a template node adding brainstorming must lift the cap"
    assert update.get("agent_question_rounds") == 15
