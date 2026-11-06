"""A template of the deployment's own routes the way its row says (#167), as a built-in specialist's did by name.

Before, only the ten built-in specialist templates skipped triage, were left
out of triage's list and were recorded as analysis: an operator's own was
triaged like any other, so its task reached the classifier with the
repository's tools.
"""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from langgraph.types import Command

from embry0.workflows.issue_to_pr import plan_route
from embry0.workflows.issue_to_pr.nodes import triage_node

#: A route of the deployment's own: triage, then one agent of its own, then the output step.
_GRAPH = {
    "nodes": [
        {"node_id": "triage", "agent_type": "triage"},
        {"node_id": "scout", "agent_type": "scout"},
        {"node_id": "output", "agent_type": "output"},
    ],
    "edges": [
        {"edge_id": "e1", "source": "triage", "target": "scout", "edge_type": "flow"},
        {"edge_id": "e2", "source": "scout", "target": "output", "edge_type": "flow"},
    ],
}
_CODE = {
    "nodes": [{"node_id": "triage", "agent_type": "triage"}, {"node_id": "dev", "agent_type": "developer"}],
    "edges": [{"edge_id": "e1", "source": "triage", "target": "dev", "edge_type": "flow"}],
}


def _row(name: str, graph: dict[str, Any], **behaviour: Any) -> dict[str, Any]:
    return {"id": name, "name": name, "description": f"the {name} route", "graph_definition": graph, **behaviour}


class _Templates:
    """The pipeline templates repository: summaries from list_all, the whole row from get."""

    def __init__(self, *rows: dict[str, Any]) -> None:
        self._rows = {row["id"]: row for row in rows}

    async def list_all(self) -> list[dict[str, Any]]:
        return [{k: v for k, v in row.items() if k != "graph_definition"} for row in self._rows.values()]

    async def get(self, template_id: str) -> dict[str, Any] | None:
        return self._rows.get(template_id)


def _state(**extra: Any) -> dict[str, Any]:
    return {
        "job_id": "job-1",
        "repo": "acme/widgets",
        "task": "Scout the widgets service for dead flags.",
        "sandbox_container_id": "container-1",
        "agent_outputs": [],
        "errors": [],
        "total_cost_usd": 0.0,
        **extra,
    }


def _triage_answers(decision: dict[str, Any]) -> AsyncMock:
    return AsyncMock(
        return_value={
            "agent_outputs": [{"agent_type": "triage", "is_error": False, "output": json.dumps(decision)}],
            "total_cost_usd": 0.0,
            "current_stage": "triage_complete",
        }
    )


async def _triage(state: dict[str, Any], templates: _Templates, run: AsyncMock) -> Any:
    config = {
        "configurable": {
            "agent_runner": MagicMock(),
            "credentials": {},
            "repo_preferences_repo": None,
            "pipeline_templates_repo": templates,
        }
    }
    with (
        patch("embry0.workflows.issue_to_pr.nodes.run_agent_node", new=run),
        patch("embry0.workflows.issue_to_pr.nodes.get_stream_writer", return_value=lambda _e: None),
    ):
        return await triage_node(state, config)


@pytest.mark.asyncio
async def test_a_template_of_the_deployments_own_marked_fixed_route_skips_triage() -> None:
    templates = _Templates(_row("scout-pass", _GRAPH, fixed_route=True, offered_to_triage=False, job_kind="analysis"))
    run = _triage_answers({"action": "proceed", "confidence": 0.9, "job_kind": "code", "reasoning": "x"})

    update = await _triage(_state(pipeline_template_request="scout-pass"), templates, run)

    assert run.await_count == 0, "a job naming a template whose row fixes its route ran the triage agent"
    assert isinstance(update, Command) and update.goto == "plan_route", f"triage did not hand over: {update!r}"
    decision = update.update["triage_decision"]
    assert (decision["pipeline_template"], decision["job_kind"]) == ("scout-pass", "analysis"), decision


@pytest.mark.asyncio
async def test_a_fixed_route_that_says_no_kind_leaves_triages_default_kind() -> None:
    templates = _Templates(_row("quick-scout", _CODE, fixed_route=True))
    run = _triage_answers({"action": "proceed", "confidence": 0.9, "job_kind": "ops", "reasoning": "x"})
    update = await _triage(_state(pipeline_template_request="quick-scout"), templates, run)
    assert run.await_count == 0, "a fixed route ran the triage agent"
    assert isinstance(update, Command), f"triage did not hand over: {update!r}"
    assert update.update["triage_decision"]["job_kind"] == "code"


@pytest.mark.asyncio
async def test_a_template_whose_row_does_not_fix_its_route_is_triaged() -> None:
    """A built-in specialist template an operator unmarked is triaged like any other: the row decides, not the
    name."""
    from embry0.storage.seeds.pipeline_templates_builtin import BUILTIN_PIPELINE_TEMPLATES

    why = BUILTIN_PIPELINE_TEMPLATES["why"]
    templates = _Templates(
        _row("why", why["graph_definition"], fixed_route=False, offered_to_triage=False, job_kind="analysis")
    )
    run = _triage_answers({"action": "proceed", "confidence": 0.9, "job_kind": "analysis", "reasoning": "x"})

    await _triage(_state(pipeline_template_request="why"), templates, run)

    assert run.await_count == 1, "the row says the route is not fixed, so triage decides"


@pytest.mark.asyncio
async def test_triage_is_not_offered_a_template_whose_row_says_so() -> None:
    templates = _Templates(
        _row("scout-pass", _GRAPH, offered_to_triage=False),
        _row("scout-open", _GRAPH),
        _row("quick-fix", _CODE),
    )
    run = _triage_answers({"action": "proceed", "confidence": 0.9, "job_kind": "code", "reasoning": "x"})

    await _triage(_state(), templates, run)

    prompt = run.await_args_list[0].kwargs["prompt"]
    listed = prompt.split("Available pipeline templates:\n", 1)[1].split("\n\n", 1)[0]
    names = {line[2:].split()[0].rstrip(":") for line in listed.splitlines()}
    assert names == {"scout-open", "quick-fix"}, f"triage was offered {sorted(names)}"


@pytest.fixture
def _no_stream_writer():
    with patch("embry0.workflows.issue_to_pr.plan_route.get_stream_writer", return_value=lambda _e: None):
        yield


@pytest.mark.asyncio
@pytest.mark.usefixtures("_no_stream_writer")
async def test_triages_pick_of_a_template_it_is_not_offered_is_dropped() -> None:
    templates = _Templates(
        _row("scout-pass", _GRAPH, offered_to_triage=False, job_kind="analysis"),
        _row("quick-fix", _CODE, default_for_kind="code"),
    )
    config = {"configurable": {"pipeline_templates_repo": templates, "jobs_repo": None, "db": None}}
    state = {"job_id": "job-1", "triage_decision": {"job_kind": "code", "pipeline_template": "scout-pass"}}

    # The scout runs in jobs, so the template is valid: only its row keeps triage's pick from running.
    with patch.object(plan_route, "_known_agent_types", AsyncMock(return_value={"scout"})):
        out = await plan_route.plan_route_node(state, config)
        assert out["route_template_name"] == "quick-fix", "triage's pick of a template it is not offered ran"

        templates._rows["scout-pass"]["offered_to_triage"] = True
        offered = await plan_route.plan_route_node(state, config)
    assert offered["route_template_name"] == "scout-pass", "triage's pick of a template it is offered runs"


@pytest.mark.asyncio
@pytest.mark.usefixtures("_no_stream_writer")
async def test_a_named_template_records_the_kind_its_row_says() -> None:
    templates = _Templates(
        _row("scout-pass", _GRAPH, fixed_route=True, offered_to_triage=False, job_kind="research"),
        _row("quick-fix", _CODE, default_for_kind="code"),
    )
    config = {"configurable": {"pipeline_templates_repo": templates, "jobs_repo": None, "db": None}}
    state = {
        "job_id": "job-1",
        "triage_decision": {"job_kind": "code", "pipeline_template": "scout-pass"},
        "pipeline_template_request": "scout-pass",
    }

    with patch.object(plan_route, "_known_agent_types", AsyncMock(return_value={"scout"})):
        out = await plan_route.plan_route_node(state, config)

    assert out["route_template_name"] == "scout-pass"
    assert out["job_kind"] == "research", "the row's job_kind, not triage's code"


_SPECIALISTS = {
    "nodes": [
        {"node_id": "triage", "agent_type": "triage"},
        {"node_id": "why", "agent_type": "why"},
        {"node_id": "how", "agent_type": "how"},
        {"node_id": "output", "agent_type": "output"},
    ],
    "edges": [
        {"edge_id": "e1", "source": "triage", "target": "why", "edge_type": "flow"},
        {"edge_id": "e2", "source": "why", "target": "how", "edge_type": "flow"},
        {"edge_id": "e3", "source": "how", "target": "output", "edge_type": "flow"},
    ],
}
_WHY_THEN_CODE = {
    "nodes": [
        {"node_id": "triage", "agent_type": "triage"},
        {"node_id": "why", "agent_type": "why"},
        {"node_id": "dev", "agent_type": "developer"},
    ],
    "edges": [
        {"edge_id": "e1", "source": "triage", "target": "why", "edge_type": "flow"},
        {"edge_id": "e2", "source": "why", "target": "dev", "edge_type": "flow"},
    ],
}


async def _planned(templates: _Templates, name: str) -> dict[str, Any]:
    jobs = AsyncMock()
    config = {"configurable": {"pipeline_templates_repo": templates, "jobs_repo": jobs, "db": None}}
    state = {
        "job_id": "job-1",
        "task": "t",
        "triage_decision": {"job_kind": "code", "pipeline_template": name},
        "pipeline_template_request": name,
    }
    out = await plan_route.plan_route_node(state, config)
    return {**out, "recorded": jobs.update.await_args.kwargs}


@pytest.mark.asyncio
@pytest.mark.usefixtures("_no_stream_writer")
async def test_a_new_template_of_specialists_only_records_its_jobs_as_analysis() -> None:
    """Probe C: a template written after the upgrade, every step a specialist and no job_kind, is analysis,
    as the route made it before #167, however it is named."""
    out = await _planned(_Templates(_row("explain-twice", _SPECIALISTS)), "explain-twice")
    assert out["job_kind"] == "analysis", "a route of specialists only is analysis"
    assert out["recorded"]["job_kind"] == "analysis"


@pytest.mark.asyncio
@pytest.mark.usefixtures("_no_stream_writer")
async def test_a_specialist_route_given_a_coding_step_is_no_longer_analysis() -> None:
    """Probe D: the built-in why template edited to add a developer step records triage's kind: the kind is
    read from the route as it is now, and only an operator's job_kind overrides it."""
    from embry0.workflows.row_behaviour import builtin_template

    # The built-in row as the seed wrote it, its graph edited since.
    edited = {**(builtin_template("why") or {}), "id": "why", "graph_definition": _WHY_THEN_CODE}
    out = await _planned(_Templates(edited), "why")
    assert out["job_kind"] == "code", "an edited route keeps no stale analysis kind"
    overridden = await _planned(_Templates({**edited, "job_kind": "research"}), "why")
    assert overridden["job_kind"] == "research", "the operator's job_kind overrides what the route makes it"


@pytest.mark.asyncio
@pytest.mark.usefixtures("_no_stream_writer")
async def test_plan_route_records_who_codes_the_job_and_who_leads_its_template() -> None:
    slop_route = {
        "nodes": [
            {"node_id": "triage", "agent_type": "triage"},
            {"node_id": "slop", "agent_type": "slopmaster"},
        ],
        "edges": [{"edge_id": "e1", "source": "triage", "target": "slop", "edge_type": "flow"}],
    }
    out = await _planned(_Templates(_row("tidy", slop_route, leader_agent="slopmaster")), "tidy")
    assert (out["recorded"]["coding_agent"], out["recorded"]["leader_agent"]) == ("slopmaster", "slopmaster")
    fix = await _planned(_Templates(_row("quick-fix", _CODE)), "quick-fix")
    assert (fix["recorded"]["coding_agent"], fix["recorded"]["leader_agent"]) == ("developer", None)


class _Unreadable(_Templates):
    async def list_all(self) -> list[dict[str, Any]]:
        raise ConnectionError("the database is gone")


@pytest.mark.asyncio
async def test_a_failed_read_fixes_no_route_and_triage_runs() -> None:
    """When the templates cannot be read nothing is known: the seed's word that why is fixed is not taken,
    since the operator may have changed it (#167)."""
    run = _triage_answers({"action": "proceed", "confidence": 0.9, "job_kind": "analysis", "reasoning": "x"})
    await _triage(_state(pipeline_template_request="why"), _Unreadable(), run)
    assert run.await_count == 1, "a failed read fixed the route from the seed"
    prompt = run.await_args_list[0].kwargs["prompt"]
    assert "Available pipeline templates" not in prompt, "a template list that could not be read offers none"


@pytest.mark.asyncio
async def test_triage_may_not_pick_a_template_whose_row_cannot_be_read() -> None:
    assert await plan_route._offered_to_triage(_Unreadable(), "scout-pass") is False
    assert await plan_route._offered_to_triage(None, "why") is False, "without a database the seed says it"
    assert await plan_route._offered_to_triage(None, "quick-fix") is True
    assert await plan_route._offered_to_triage(_Templates(), "unknown") is True, "a name with no row is not found later"
