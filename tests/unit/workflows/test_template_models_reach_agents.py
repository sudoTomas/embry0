"""A pipeline template's model choice reaches every agent it can name (#468).

A template names models on its nodes (``config.model``). Every non-triage
node is a route step whose config reaches its agent through
``step_template_config``; the triage root reaches triage through
``triage_template_config`` when the job names its template. Each case runs
the real resolver, so a mapping that drops a type fails here.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from embry0.agents.resolver import resolve_agent_config
from embry0.workflows.issue_to_pr.route_plan import (
    build_route_plan,
    step_template_config,
    triage_template_config,
)

# Template node type -> the runtime agent type that runs it.
_STEP_TYPES = {
    "developer": "developer",
    "reviewer": "review",
    "qa": "qa",
    "research": "research",
    "analysis": "analysis",
    "ops": "ops",
}


def _graph(types: list[str]) -> dict[str, Any]:
    nodes = [{"node_id": t, "agent_type": t, "config": {"model": f"model-for-{t}"}} for t in types]
    edges = [
        {"edge_id": f"e{i}", "source": a, "target": b, "edge_type": "flow"}
        for i, (a, b) in enumerate(zip(types, types[1:], strict=False))
    ]
    return {"nodes": nodes, "edges": edges}


def _resolved_model(agent_type: str, template_config: dict[str, Any] | None) -> str:
    definition = {"model": "definition-model", "tools": [], "skills": []}
    return resolve_agent_config(agent_type, definition, template_config, default_model="deployment-default").model


@pytest.mark.parametrize(("node_type", "runtime_type"), sorted(_STEP_TYPES.items()))
def test_a_step_model_reaches_its_agent(node_type: str, runtime_type: str) -> None:
    plan = build_route_plan(_graph(["triage", node_type]))
    state = {"route_plan": plan, "route_cursor": 0}
    got = _resolved_model(runtime_type, step_template_config(state, runtime_type))
    assert got == f"model-for-{node_type}", f"a {node_type} step's model resolved to {got!r}"


def _legacy_graph(types: list[str]) -> dict[str, Any]:
    """The editor's pre-#78 shape: overrides at the node's top level, no ``config``."""
    graph = _graph(types)
    graph["nodes"] = [{"node_id": t, "agent_type": t, "model": f"model-for-{t}"} for t in types]
    return graph


@pytest.mark.parametrize(("node_type", "runtime_type"), sorted(_STEP_TYPES.items()))
def test_a_legacy_top_level_step_model_reaches_its_agent(node_type: str, runtime_type: str) -> None:
    plan = build_route_plan(_legacy_graph(["triage", node_type]))
    state = {"route_plan": plan, "route_cursor": 0}
    got = _resolved_model(runtime_type, step_template_config(state, runtime_type))
    assert got == f"model-for-{node_type}", f"a legacy-shaped {node_type} step's model resolved to {got!r}"


def test_a_legacy_top_level_triage_model_reaches_triage() -> None:
    got = _resolved_model("triage", triage_template_config(_legacy_graph(["triage", "developer"])))
    assert got == "model-for-triage", f"a legacy-shaped triage model resolved to {got!r}"


def _editor_node(node_id: str, agent_type: str) -> dict[str, Any]:
    """A node exactly as ``serializeGraph`` (frontend/src/lib/graph-utils.ts) writes it today.

    Every override sits at the top level, and ``AgentDetailPopup`` stores
    skills as ``{name, mode}`` objects.
    """
    return {
        "node_id": node_id,
        "agent_type": agent_type,
        "label": agent_type,
        "position": {"x": 0, "y": 0},
        "model": f"model-for-{agent_type}",
        "max_budget_usd": 2.5,
        "max_turns": 40,
        "effort": "high",
        "tools": ["Read", "Edit"],
        "skills": [
            {"name": "superpowers:brainstorming", "mode": "autonomous"},
            {"name": "superpowers:writing-plans", "mode": "autonomous"},
        ],
        "prompt_prepend": "be brief",
        "prompt_append": None,
        "custom_prompt": None,
        "custom_tools": None,
        "sandbox": None,
    }


def test_editor_shaped_skill_objects_resolve_to_their_names() -> None:
    graph = _graph(["triage", "developer"])
    graph["nodes"] = [_editor_node("triage", "triage"), _editor_node("developer", "developer")]
    plan = build_route_plan(graph)
    template_config = step_template_config({"route_plan": plan, "route_cursor": 0}, "developer")
    definition = {"model": "definition-model", "tools": [], "skills": ["superpowers:test-driven-development"]}
    resolved = resolve_agent_config("developer", definition, template_config, default_model="deployment-default")
    assert resolved.skills == ["superpowers:brainstorming", "superpowers:writing-plans"], (
        f"editor-shaped skill objects resolved to {resolved.skills!r}"
    )
    assert resolved.model == "model-for-developer"


def test_the_triage_node_model_reaches_triage() -> None:
    got = _resolved_model("triage", triage_template_config(_graph(["triage", "developer"])))
    assert got == "model-for-triage", f"the template's triage model resolved to {got!r}"


def test_a_template_without_a_triage_model_leaves_triage_alone() -> None:
    graph = {"nodes": [{"node_id": "t", "agent_type": "triage"}, {"node_id": "d", "agent_type": "developer"}]}
    assert triage_template_config(graph) is None


def _templates_repo(graph: dict[str, Any]) -> MagicMock:
    repo = MagicMock()
    repo.list_all = AsyncMock(return_value=[{"id": "t1", "name": "pinned"}])
    repo.get = AsyncMock(return_value={"id": "t1", "name": "pinned", "graph_definition": graph})
    return repo


@pytest.mark.asyncio
async def test_only_a_template_the_job_named_configures_triage() -> None:
    """Triage picks the template, so only one named on the job is known in time."""
    from embry0.workflows.issue_to_pr.plan_route import pinned_triage_template_config

    repo = _templates_repo(_graph(["triage", "developer"]))
    pinned = await pinned_triage_template_config({"pipeline_template_request": "pinned"}, repo)
    assert pinned == {"agent_models": {"triage": "model-for-triage"}}
    assert await pinned_triage_template_config({}, repo) is None
    assert await pinned_triage_template_config({"pipeline_template_request": "standard"}, repo) is None


@pytest.mark.asyncio
async def test_qa_node_hands_the_step_config_to_run_agent_node() -> None:
    """The QA agent runs inside a sub-task; the step config rides the configurable."""
    from embry0.workflows.qa.nodes import qa_node

    state = {
        "job_id": "JOB1__web",
        "repo": "x/y",
        "qa": {
            "attempts": [{"attempt_n": 1, "sandbox_id": "c1", "artifact_prefix": "JOB1/web/"}],
            "qa_yaml_parsed": {},
            "acceptance_criteria": [],
        },
    }
    step_config = {"agent_models": {"qa": "model-for-qa"}}
    config = {"configurable": {"agent_runner": MagicMock(), "db": MagicMock(), "qa_template_config": step_config}}
    fake_run = AsyncMock(return_value={"agent_outputs": []})
    defs = MagicMock()
    defs.get = AsyncMock(return_value={"model": "m", "tools": [], "skills": [], "system_prompt": "sp"})

    with (
        patch("embry0.storage.repositories.agent_definitions.AgentDefinitionsRepository", return_value=defs),
        patch("embry0.orchestration.nodes.agent.run_agent_node", fake_run),
    ):
        await qa_node(state, config)

    got = fake_run.call_args.kwargs.get("template_config")
    assert got == step_config, f"qa_node ran the QA agent without the template's QA step config: {got!r}"


@pytest.mark.asyncio
async def test_branch_qa_puts_the_qa_step_config_on_the_configurable() -> None:
    import embry0.workflows.issue_to_pr.graph as graph_mod

    plan = build_route_plan(_graph(["triage", "developer", "qa"]))
    state = {"route_plan": plan, "route_cursor": 1, "qa": {}}
    seen: dict[str, Any] = {}

    async def fake_orchestrator(st: dict[str, Any], cfg: Any) -> dict[str, Any]:
        seen["configurable"] = cfg["configurable"]
        return {}

    with patch.object(graph_mod, "qa_orchestrator_node", fake_orchestrator):
        await graph_mod._branch_qa_orchestrator_node(state, {"configurable": {"job_id": "JOB1"}})

    assert seen["configurable"].get("qa_template_config") == {"agent_models": {"qa": "model-for-qa"}}, (
        f"the QA step's config never reached the QA fan-out: {seen['configurable']}"
    )
    assert seen["configurable"]["job_id"] == "JOB1"


@pytest.mark.asyncio
async def test_triage_runs_with_the_named_templates_triage_config() -> None:
    import json

    from embry0.workflows.issue_to_pr.nodes import triage_node

    decision = {
        "action": "proceed",
        "confidence": 0.9,
        "pipeline_template": "pinned",
        "pipeline_config": {"budget_usd": 1.0},
        "questions": [],
        "sub_tasks": [],
        "reasoning": "r",
    }
    run = AsyncMock(
        return_value={
            "agent_outputs": [{"agent_type": "triage", "is_error": False, "output": json.dumps(decision)}],
            "total_cost_usd": 0.0,
        }
    )
    state = {
        "job_id": "job-1",
        "repo": "o/r",
        "task": "t",
        "sandbox_container_id": "c",
        "agent_outputs": [],
        "errors": [],
        "pipeline_template_request": "pinned",
    }
    config = {
        "configurable": {
            "agent_runner": MagicMock(),
            "credentials": {},
            "repo_preferences_repo": None,
            "pipeline_templates_repo": _templates_repo(_graph(["triage", "developer"])),
        }
    }

    with (
        patch("embry0.workflows.issue_to_pr.nodes.run_agent_node", new=run),
        patch("embry0.workflows.issue_to_pr.nodes.get_stream_writer", return_value=lambda _: None),
    ):
        await triage_node(state, config)

    got = run.call_args.kwargs.get("template_config")
    assert got == {"agent_models": {"triage": "model-for-triage"}}, (
        f"triage ran without the named template's triage config: {got!r}"
    )
