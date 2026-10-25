"""A job that names a specialist template skips the triage agent: its route is already fixed.

Handed a specialist's task with the repository's tools, the classifier
started doing the task itself and timed out before deciding; and on every
such job it was a model call that changed nothing, since plan_route runs the
named template whatever triage picks.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from langgraph.types import Command

from embry0.workflows.issue_to_pr.nodes._triage import fixed_route_decision


def _state(**extra: Any) -> dict[str, Any]:
    return {
        "job_id": "job-1",
        "repo": "acme/widgets",
        "task": "Map the areas of acme/widgets: the parts its work is about.",
        "sandbox_container_id": "container-abc",
        "agent_outputs": [],
        "errors": [],
        "total_cost_usd": 0.0,
        **extra,
    }


def test_only_a_named_specialist_template_fixes_the_route():
    decision = fixed_route_decision(_state(pipeline_template_request="map-areas"))
    assert decision is not None
    assert (decision["action"], decision["job_kind"], decision["pipeline_template"]) == (
        "proceed",
        "analysis",
        "map-areas",
    )
    assert fixed_route_decision(_state(pipeline_template_request="context-audit")) is not None
    assert fixed_route_decision(_state()) is None, "a job that names nothing is triaged"
    assert fixed_route_decision(_state(pipeline_template_request="full-magnum-opus")) is None
    assert fixed_route_decision(_state(pipeline_template_request="analysis-default")) is None


def test_a_reentry_with_something_to_decide_still_asks_the_agent():
    assert fixed_route_decision(_state(pipeline_template_request="map-areas", qa={"final_status": "failed"})) is None
    assert fixed_route_decision(_state(pipeline_template_request="map-areas", user_answers=[{"q": "a"}])) is None


@pytest.mark.asyncio
async def test_the_node_hands_over_without_running_the_agent():
    from embry0.workflows.issue_to_pr.nodes import triage_node

    run = AsyncMock()
    events: list[dict[str, Any]] = []
    with (
        patch("embry0.workflows.issue_to_pr.nodes.run_agent_node", new=run),
        patch("embry0.workflows.issue_to_pr.nodes.get_stream_writer", return_value=events.append),
    ):
        update = await triage_node(
            _state(pipeline_template_request="map-areas"),
            {"configurable": {"agent_runner": MagicMock(), "credentials": {}}},
        )
    run.assert_not_awaited()
    # Triage routes only by Command: a plain update ended the job right here.
    assert isinstance(update, Command) and update.goto == "plan_route"
    assert update.update["current_stage"] == "triage_complete"
    assert update.update["triage_decision"]["pipeline_template"] == "map-areas"
    assert [e["type"] for e in events] == ["node_started", "progress", "node_completed"]
