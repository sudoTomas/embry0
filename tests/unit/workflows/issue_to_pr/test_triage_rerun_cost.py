"""Triage's re-run after a needs_info answer counts the first pass (#482).

On resume LangGraph runs triage_node again from its start: the first pass runs
(and costs) again, the interrupt returns the answers, and the node re-runs
triage with them. The re-run was given the state from before the first pass, so
the update counted the re-run alone: its total missed the first pass's cost,
and its outputs the first pass's output.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

CONFIG = {"configurable": {"agent_runner": MagicMock(), "credentials": {}, "repo_preferences_repo": None}}


def _runner(*costs: float) -> tuple[AsyncMock, list[float]]:
    """``run_agent_node`` as it reports a run: its output alone, and the state's total plus the run's cost."""
    queue, started_from = list(costs), []

    async def _impl(*_args: Any, **kwargs: Any) -> dict[str, Any]:
        cost, state = queue.pop(0), kwargs["state"]
        started_from.append(state.get("total_cost_usd", 0.0))
        return {
            "agent_outputs": [{"agent_type": "triage", "is_error": False, "output": f"run {cost}", "cost_usd": cost}],
            "total_cost_usd": state.get("total_cost_usd", 0.0) + cost,
            "current_stage": "triage_complete",
        }

    return AsyncMock(side_effect=_impl), started_from


@pytest.mark.asyncio
async def test_the_re_run_after_a_needs_info_answer_counts_the_first_pass() -> None:
    from embry0.workflows.issue_to_pr.nodes import triage_node

    state = {
        "job_id": "job-1",
        "repo": "acme/widgets",
        "task": "Add a widget",
        "sandbox_container_id": "container-1",
        "agent_outputs": [],
        "errors": [],
        "total_cost_usd": 1.0,
    }
    runner, started_from = _runner(0.10, 0.25)
    decisions = [{"action": "needs_info", "questions": ["which database?"]}, {"action": "proceed"}]
    with (
        patch("embry0.workflows.issue_to_pr.nodes.run_agent_node", new=runner),
        patch("embry0.orchestration.nodes.triage.parse_triage_response", side_effect=decisions),
        patch("embry0.workflows.issue_to_pr.nodes.interrupt", return_value="Postgres"),
        patch("embry0.workflows.issue_to_pr.nodes.get_stream_writer", return_value=lambda _e: None),
    ):
        result = await triage_node(state, CONFIG)

    update = getattr(result, "update", result)
    assert started_from == [1.0, pytest.approx(1.10)], f"the re-run's budget check started from {started_from[1:]}"
    assert update["total_cost_usd"] == pytest.approx(1.35), (
        f"the job's total after both runs: {update['total_cost_usd']}"
    )
    outputs = [o["output"] for o in update["agent_outputs"]]
    assert outputs == ["run 0.1", "run 0.25"], f"the first pass's output is kept beside the re-run's: {outputs}"
