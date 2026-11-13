"""An exhausted triage keeps its run's record (#444).

When triage asks past the job's ask-user cap, the node routes to max_retries
with the cap's update. Its run's output, cost and own error went with it no
further: the update held the cap's keys alone. Both of triage's cap checks,
on its first pass and on the re-run after a person answered it, now lay the
cap's update over the run's (``with_cap_updates``), the run's error first.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from langgraph.types import Command

from embry0.orchestration.nodes.agent import DEFAULT_ASK_USER_CAP

ASK = {"type": "agent_ask_user", "question": "which database?", "category": "general"}
RUN_ERROR = "triage: one of its tool calls failed"


def _run(output: str, cost: float, *, ask: bool) -> dict[str, Any]:
    """A triage run that ended with ``output``, cost ``cost`` and an error of its own, asking a person if ``ask``."""
    return {
        "agent_outputs": [{"agent_type": "triage", "is_error": False, "output": output, "cost_usd": cost}],
        "total_cost_usd": cost,
        "errors": [RUN_ERROR],
        "current_stage": "triage_complete",
        "_ask": ask,
    }


def _runner(*runs: dict[str, Any]) -> AsyncMock:
    queue = list(runs)

    async def _impl(*_args: Any, **kwargs: Any) -> dict[str, Any]:
        run = dict(queue.pop(0))
        if run.pop("_ask") and kwargs.get("on_event") is not None:
            kwargs["on_event"](ASK)
        return run

    return AsyncMock(side_effect=_impl)


def _state(**over: Any) -> dict[str, Any]:
    return {
        "job_id": "job-1",
        "repo": "acme/widgets",
        "task": "Add a widget",
        "sandbox_container_id": "container-1",
        "agent_outputs": [],
        "errors": [],
        "total_cost_usd": 0.0,
        "agent_question_rounds": DEFAULT_ASK_USER_CAP,  # the cap is reached: the next question exhausts it
        "agent_questions_exhausted": False,
        **over,
    }


CONFIG = {"configurable": {"agent_runner": MagicMock(), "credentials": {}, "repo_preferences_repo": None}}


def _kept(update: dict[str, Any], outputs: list[str], cost: float) -> list[str]:
    problems = []
    if [o.get("output") for o in update.get("agent_outputs") or []] != outputs:
        problems.append(f"agent_outputs: {update.get('agent_outputs')}")
    if update.get("total_cost_usd") != cost:
        problems.append(f"total_cost_usd: {update.get('total_cost_usd')}")
    errors = update.get("errors") or []
    if len(errors) != 2 or errors[0] != RUN_ERROR:
        problems.append(f"errors (the run's, then the cap's): {errors}")
    if update.get("error_code") != "ERR_MAX_AGENT_QUESTIONS":
        problems.append(f"error_code: {update.get('error_code')}")
    return problems


@pytest.mark.asyncio
async def test_triage_past_the_cap_on_its_first_pass_keeps_its_run() -> None:
    from embry0.workflows.issue_to_pr.nodes import triage_node

    with (
        patch("embry0.workflows.issue_to_pr.nodes.run_agent_node", new=_runner(_run("first", 0.42, ask=True))),
        patch("embry0.orchestration.nodes.triage.parse_triage_response", return_value={"action": "proceed"}),
        patch("embry0.workflows.issue_to_pr.nodes.get_stream_writer", return_value=lambda _e: None),
    ):
        result = await triage_node(_state(), CONFIG)

    assert isinstance(result, Command) and result.goto == "max_retries", result
    problems = _kept(result.update, ["first"], 0.42)
    assert not problems, "the exhausted update lost the run's record: " + "; ".join(problems)


@pytest.mark.asyncio
async def test_triage_past_the_cap_on_its_re_run_keeps_the_re_run() -> None:
    from embry0.workflows.issue_to_pr.nodes import triage_node

    decisions = [{"action": "needs_info", "questions": ["which database?"]}, {"action": "proceed"}]
    with (
        patch(
            "embry0.workflows.issue_to_pr.nodes.run_agent_node",
            new=_runner(_run("first", 0.1, ask=False), _run("again", 0.25, ask=True)),
        ),
        patch("embry0.orchestration.nodes.triage.parse_triage_response", side_effect=decisions),
        patch("embry0.workflows.issue_to_pr.nodes.interrupt", return_value="Postgres"),
        patch("embry0.workflows.issue_to_pr.nodes.get_stream_writer", return_value=lambda _e: None),
    ):
        result = await triage_node(_state(), CONFIG)

    assert isinstance(result, Command) and result.goto == "max_retries", result
    # The re-run's record beside the first pass's output, which the re-run keeps (#482).
    problems = _kept(result.update, ["first", "again"], 0.25)
    assert not problems, "the exhausted update lost the re-run's record: " + "; ".join(problems)
