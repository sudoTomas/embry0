"""Continuing a job from a budget pause lets it spend more (the person deciding is the one paying).

The fleet ceiling bounds a cap triage or a job-create field picks; a person's
continue goes on top of it, unless the deployment's ``budget`` section makes
the ceiling a hard limit (#171). Without the raise, a job over its cap went
straight back to the pause after every continued run.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import patch

import pytest

import embry0.workflows.issue_to_pr.nodes as nodes_module
from embry0.orchestration.budget import allowance_to_continue, resolve_job_cap
from embry0.orchestration.routing.conditions import check_budget
from embry0.settings.sections.budget import BudgetSettings

OVER = {"pipeline_config": {"budget_usd": 10.0}, "total_cost_usd": 11.49, "budget_overrun_usd": 1.49}


def test_an_allowance_goes_on_top_of_the_ceiling() -> None:
    assert resolve_job_cap({"pipeline_config": {"budget_usd": 25.0}}, ceiling=10.0) == 10.0
    assert resolve_job_cap(
        {"pipeline_config": {"budget_usd": 25.0}, "budget_allowance_usd": 11.49}, ceiling=10.0
    ) == pytest.approx(21.49)


def test_continuing_lets_the_job_spend_one_more_cap_or_what_the_person_named() -> None:
    assert allowance_to_continue(OVER, ceiling=10.0) == 11.49
    assert resolve_job_cap({**OVER, "budget_allowance_usd": 11.49}, ceiling=10.0) == pytest.approx(21.49)
    assert allowance_to_continue(OVER, ceiling=10.0, extra=25.0) == 26.49
    assert allowance_to_continue({**OVER, "budget_allowance_usd": 40.0}, ceiling=10.0) == 40.0, "never lowered"


def test_the_raised_cap_decides_once_a_person_granted_an_allowance() -> None:
    assert check_budget(OVER) == "over_budget"
    raised = {**OVER, "budget_allowance_usd": 11.49}
    assert check_budget(raised) == "within_budget", "the old overrun predates the allowance"
    assert check_budget({**raised, "total_cost_usd": 21.6}) == "over_budget"


def _run(
    state: dict[str, Any], answer: Any, budget: BudgetSettings | None = None
) -> tuple[dict[str, Any], dict[str, Any]]:
    seen: dict[str, Any] = {}
    section = budget or BudgetSettings(max_budget_per_job_usd=10.0)

    def _interrupt(summary: dict[str, Any]) -> Any:
        seen.update(summary)
        return answer

    async def _settings(config: Any) -> BudgetSettings:
        return section

    async def go() -> dict[str, Any]:
        with (
            patch.object(nodes_module, "get_stream_writer", return_value=lambda _: None),
            patch.object(nodes_module, "interrupt", new=_interrupt),
            patch("embry0.workflows.issue_to_pr.nodes._interrupts.load_budget_settings", new=_settings),
        ):
            return await nodes_module.max_retries_node(state, config=None)

    import asyncio

    return asyncio.run(go()), seen


def test_a_budget_pause_says_what_continuing_adds_and_continuing_grants_it() -> None:
    state = {**OVER, "job_id": "job-1", "current_stage": "developer_complete", "retry_count": 1}
    updates, summary = _run(state, {"choice": "continue_retrying"})
    assert summary["budget"] == {"spent": 11.49, "cap": 10.0, "continue_adds": 10.0}
    assert updates["budget_allowance_usd"] == 11.49 and updates["current_stage"] == "developer_retry"

    updates, _ = _run(state, {"choice": "continue_retrying", "extra_budget_usd": 5})
    assert updates["budget_allowance_usd"] == 6.49


@pytest.mark.parametrize("choice", ["abandon", "merge_as_is"])
def test_other_answers_grant_nothing(choice: str) -> None:
    updates, _ = _run({**OVER, "job_id": "job-1", "current_stage": "developer_complete"}, {"choice": choice})
    assert "budget_allowance_usd" not in updates


def test_a_pause_within_budget_carries_no_budget_line_and_continuing_grants_nothing() -> None:
    state = {
        "pipeline_config": {"budget_usd": 10.0},
        "total_cost_usd": 3.0,
        "job_id": "job-1",
        "current_stage": "dev_branch_missing",
    }
    updates, summary = _run(state, {"choice": "continue_retrying"})
    assert "budget" not in summary and "budget_allowance_usd" not in updates


def test_the_budget_pause_names_the_cap_in_force_not_the_one_triage_asked_for() -> None:
    state = {"pipeline_config": {"budget_usd": 35.0}, "total_cost_usd": 16.15, "budget_overrun_usd": 6.15}
    state.update(job_id="job-1", current_stage="developer_complete")
    _, summary = _run(state, {"choice": "abandon"})
    assert summary["message"].startswith("The job reached its budget ($16.15 of $10.00)")
    assert summary["budget"]["cap"] == 10.0


@pytest.mark.parametrize("stage", ["dev_run_unfinished", "dev_branch_missing", "review_errored"])
def test_a_job_over_budget_that_paused_for_another_reason_still_gets_its_budget_line(stage: str) -> None:
    """The budget line is written whatever the pause's own message: no stage may leave it unbound."""
    state = {**OVER, "job_id": "job-1", "current_stage": stage, "branch_name": "embry0/x"}
    updates, summary = _run(state, {"choice": "continue_retrying"})
    assert summary["budget"] == {"spent": 11.49, "cap": 10.0, "continue_adds": 10.0}
    assert updates["budget_allowance_usd"] == 11.49
