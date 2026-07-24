"""Generic agent node — non-code route steps (RAV-604)."""

from unittest.mock import patch

import pytest

from embry0.storage.repositories.agent_definitions import BUILTIN_SEED
from embry0.workflows.issue_to_pr.generic_agent import (
    generic_agent_node,
    route_after_generic_agent,
)


@pytest.fixture(autouse=True)
def _no_stream_writer():
    with patch(
        "embry0.workflows.issue_to_pr.generic_agent.get_stream_writer",
        return_value=lambda _e: None,
    ):
        yield


def _state(agent_type="research", cursor=0, extra_steps=(), **overrides):
    plan = [{"agent_type": agent_type, "node_id": agent_type, "config": {}}]
    plan += [{"agent_type": t, "node_id": t, "config": {}} for t in extra_steps]
    state = {
        "job_id": "job-1",
        "task": "Summarize the staged document",
        "repo": "",
        "route_plan": plan,
        "route_cursor": cursor,
        "qa": {},
    }
    state.update(overrides)
    return state


def _config():
    # No "db" in configurable → load_agent_definition returns the fallback
    # (BUILTIN_SEED), no repository access needed.
    return {"configurable": {"agent_runner": object(), "credentials": {}}}


def _stub_run(captured, *, is_error=False):
    async def run(**kwargs):
        captured.update(kwargs)
        agent_type = kwargs["agent_type"]
        stage = f"{agent_type}_failed" if is_error else f"{agent_type}_complete"
        updates = {
            "agent_outputs": [{"agent_type": agent_type, "is_error": is_error, "output": "findings"}],
            "total_cost_usd": 0.1,
            "current_stage": stage,
        }
        if is_error:
            updates["errors"] = [f"{agent_type}: boom"]
        return updates

    return run


@pytest.mark.asyncio
async def test_success_runs_step_agent_and_advances_cursor():
    captured: dict = {}
    with patch("embry0.workflows.issue_to_pr.generic_agent.run_agent_node", _stub_run(captured)):
        out = await generic_agent_node(_state(extra_steps=("output",)), _config())

    assert captured["agent_type"] == "research"
    assert captured["agent_definition"] == BUILTIN_SEED["research"]
    assert "Summarize the staged document" in captured["prompt"]
    assert out["current_stage"] == "research_complete"
    assert out["route_cursor"] == 1
    # Post-step position dispatches the trailing output step.
    assert route_after_generic_agent({**_state(extra_steps=("output",)), **out}) == "output"


@pytest.mark.asyncio
async def test_each_generic_type_loads_its_own_definition():
    for agent_type in ("research", "analysis", "ops"):
        captured: dict = {}
        with patch("embry0.workflows.issue_to_pr.generic_agent.run_agent_node", _stub_run(captured)):
            out = await generic_agent_node(_state(agent_type=agent_type), _config())
        assert captured["agent_definition"] == BUILTIN_SEED[agent_type]
        assert out["current_stage"] == f"{agent_type}_complete"


@pytest.mark.asyncio
async def test_failure_keeps_cursor_and_terminates():
    captured: dict = {}
    with patch("embry0.workflows.issue_to_pr.generic_agent.run_agent_node", _stub_run(captured, is_error=True)):
        out = await generic_agent_node(_state(extra_steps=("output",)), _config())

    assert out["current_stage"] == "research_failed"
    assert "route_cursor" not in out
    assert out["errors"] == ["research: boom"]
    assert route_after_generic_agent({**_state(extra_steps=("output",)), **out}) == "end"


@pytest.mark.asyncio
async def test_cursor_mismatch_fails_loudly():
    # Cursor pointing at a developer step must not run any agent here.
    state = _state()
    state["route_plan"] = [{"agent_type": "developer", "node_id": "d", "config": {}}]
    out = await generic_agent_node(state, _config())
    assert out["current_stage"] == "generic_agent_failed"
    assert route_after_generic_agent({**state, **out}) == "end"


@pytest.mark.asyncio
async def test_cursor_out_of_range_fails_loudly():
    out = await generic_agent_node(_state(cursor=7), _config())
    assert out["current_stage"] == "generic_agent_failed"


@pytest.mark.asyncio
async def test_prompt_includes_context_blocks():
    captured: dict = {}
    state = _state(
        repo="owner/repo",
        issue_number=42,
        additional_context="Prior Q&A",
        triage_decision={"reasoning": "It is a research ask"},
    )
    with patch("embry0.workflows.issue_to_pr.generic_agent.run_agent_node", _stub_run(captured)):
        await generic_agent_node(state, _config())
    prompt = captured["prompt"]
    assert "Repository: owner/repo" in prompt
    assert "GitHub Issue: #42" in prompt
    assert "Prior Q&A" in prompt
    assert "It is a research ask" in prompt
    assert "/workspace" in prompt


def test_route_after_generic_agent_chains_generic_steps():
    state = {
        "route_plan": [
            {"agent_type": "research", "node_id": "r", "config": {}},
            {"agent_type": "analysis", "node_id": "a", "config": {}},
        ],
        "route_cursor": 1,
        "current_stage": "research_complete",
        "qa": {},
    }
    assert route_after_generic_agent(state) == "agent"
