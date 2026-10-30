"""How far a person may continue a job past its cap is the deployment's ``budget`` section (#171).

- ``continue_may_exceed_ceiling: false`` makes the fleet ceiling a hard limit:
  a continue adds only what is left under it, and a job at it is not offered one.
- ``continue_default_extra_usd`` is what a continue adds when the person names
  no amount; ``continue_extra_max_usd`` bounds what they name.
- Every raise writes a ``job.budget_raised`` audit row naming who continued the
  job, what it had spent, and its cap before and after: settings revisions
  record changes to the caps, not a person letting one job spend more.

The defaults are today's behaviour, pinned by ``test_budget_raise_on_continue.py``.
"""

from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import patch

import pytest

import embry0.workflows.issue_to_pr.nodes as nodes_module
from embry0.orchestration import budget as budget_module
from embry0.orchestration.budget import allowance_to_continue, resolve_job_cap
from embry0.settings.sections import CATALOG


def BudgetSettings(**values: Any) -> Any:  # noqa: N802 - stands in for the section's model
    """The ``budget`` section with ``values``; the test fails here, with its own message, on a tree without it."""
    section = CATALOG.get("budget")
    assert section is not None, "budget is a section of the settings document"
    return section.model(**values)


def continue_adds(state: dict[str, Any], **kwargs: Any) -> float:
    adds = getattr(budget_module, "continue_adds", None)
    assert adds is not None, "what a continue adds is worked out by the budget section's continue policy"
    return float(adds(state, **kwargs))


OVER = {
    "pipeline_config": {"budget_usd": 6.0},
    "total_cost_usd": 6.4,
    "budget_overrun_usd": 0.4,
    "job_id": "job-1",
    "issue_id": "iss-1",
    "current_stage": "developer_complete",
    "retry_count": 1,
}


def _run(
    state: dict[str, Any], answer: Any, budget: Any
) -> tuple[dict[str, Any], dict[str, Any], list[tuple[str, str, dict[str, Any]]]]:
    seen: dict[str, Any] = {}
    audits: list[tuple[str, str, dict[str, Any]]] = []

    def _interrupt(summary: dict[str, Any]) -> Any:
        seen.update(summary)
        return answer

    async def _settings(config: Any) -> Any:
        return budget

    async def _audit(db: Any, action: str, *, actor: str = "system", details: Any = None, **kw: Any) -> None:
        audits.append((action, actor, dict(details or {})))

    async def go() -> dict[str, Any]:
        with (
            patch.object(nodes_module, "get_stream_writer", return_value=lambda _: None),
            patch.object(nodes_module, "interrupt", new=_interrupt),
            patch("embry0.workflows.issue_to_pr.nodes._interrupts.load_budget_settings", new=_settings, create=True),
            patch("embry0.audit.helpers.emit_audit", new=_audit),
        ):
            return await nodes_module.max_retries_node(state, config=None)

    return asyncio.run(go()), seen, audits


def test_a_hard_ceiling_bounds_the_cap_and_what_a_continue_adds() -> None:
    over = {"pipeline_config": {"budget_usd": 6.0}, "total_cost_usd": 6.4}
    assert continue_adds(over, ceiling=10.0) == pytest.approx(6.0), "the default: one more cap"
    assert continue_adds(over, ceiling=10.0, may_exceed_ceiling=False) == pytest.approx(3.6), "what is left under it"
    assert allowance_to_continue(over, ceiling=10.0, may_exceed_ceiling=False) == pytest.approx(4.0)
    assert continue_adds({**over, "total_cost_usd": 10.2}, ceiling=10.0, may_exceed_ceiling=False) == 0.0
    state = {"pipeline_config": {"budget_usd": 6.0}, "total_cost_usd": 6.4, "budget_allowance_usd": 6.4}
    assert resolve_job_cap(state, ceiling=10.0) == pytest.approx(12.4), "the default: on top of the ceiling"
    assert resolve_job_cap(state, ceiling=10.0, may_exceed_ceiling=False) == 10.0
    assert resolve_job_cap(state, ceiling=0.0, may_exceed_ceiling=False) == pytest.approx(12.4), "no ceiling, no bound"


def test_a_continue_past_a_hard_ceiling_adds_only_what_is_left_under_it() -> None:
    budget = BudgetSettings(max_budget_per_job_usd=10.0, continue_may_exceed_ceiling=False)
    updates, summary, _ = _run(OVER, {"choice": "continue_retrying", "actor": "alex@example.com"}, budget)
    assert summary["budget"] == {"spent": 6.4, "cap": 6.0, "continue_adds": 3.6}
    assert "continue_retrying" in summary["options"]
    assert resolve_job_cap(
        {**OVER, "budget_allowance_usd": updates["budget_allowance_usd"]}, ceiling=10.0, may_exceed_ceiling=False
    ) == pytest.approx(10.0), "the continued job may spend up to the ceiling, not one more cap past it"


def test_a_job_at_a_hard_ceiling_is_not_offered_a_continue() -> None:
    budget = BudgetSettings(max_budget_per_job_usd=6.0, continue_may_exceed_ceiling=False)
    updates, summary, audits = _run(OVER, {"choice": "continue_retrying"}, budget)
    assert "continue_retrying" not in summary["options"], summary["options"]
    assert "ceiling of $6.00" in summary["message"] and "continue_may_exceed_ceiling" in summary["message"]
    assert updates["current_stage"] == "abandoned", "a continue it was not offered changes nothing"
    assert "budget_allowance_usd" not in updates and audits == []


def test_the_default_extra_is_what_a_continue_adds_when_the_person_names_none() -> None:
    budget = BudgetSettings(continue_default_extra_usd=1.5)
    updates, summary, _ = _run(OVER, {"choice": "continue_retrying"}, budget)
    assert summary["budget"]["continue_adds"] == 1.5
    assert updates["budget_allowance_usd"] == pytest.approx(1.9), "spent 6.40 + 1.50 = a cap of 7.90 over a base of 6"
    updates, _, _ = _run(OVER, {"choice": "continue_retrying", "extra_budget_usd": 3}, budget)
    assert updates["budget_allowance_usd"] == pytest.approx(3.4), "an amount the person named wins"


def test_an_extra_above_what_a_person_may_name_is_held_to_it() -> None:
    budget = BudgetSettings(continue_extra_max_usd=2.0)
    updates, _, audits = _run(OVER, {"choice": "continue_retrying", "extra_budget_usd": 500}, budget)
    assert updates["budget_allowance_usd"] == pytest.approx(2.4), "500 is held to the section's 2"
    assert audits[0][2]["extra_usd"] == 2.0


def test_a_raise_is_audited_with_who_what_was_spent_and_the_cap_before_and_after() -> None:
    budget = BudgetSettings(max_budget_per_job_usd=10.0)
    _, _, audits = _run(OVER, {"choice": "continue_retrying", "actor": "alex@example.com"}, budget)
    assert audits == [
        (
            "job.budget_raised",
            "alex@example.com",
            {"job_id": "job-1", "spent_usd": 6.4, "cap_before_usd": 6.0, "cap_after_usd": 12.4},
        )
    ]


@pytest.mark.parametrize("choice", ["abandon", "merge_as_is"])
def test_no_raise_no_audit_row(choice: str) -> None:
    _, _, audits = _run(OVER, {"choice": choice, "actor": "alex@example.com"}, BudgetSettings())
    assert audits == []


def test_a_continue_within_budget_raises_nothing_and_audits_nothing() -> None:
    within = {**OVER, "total_cost_usd": 2.0, "budget_overrun_usd": 0.0, "current_stage": "dev_branch_missing"}
    updates, _, audits = _run(within, {"choice": "continue_retrying", "extra_budget_usd": 1}, BudgetSettings())
    assert "budget_allowance_usd" not in updates and audits == []


def test_a_continue_with_no_amount_named_adds_at_most_what_a_person_may_name() -> None:
    """The reviewer's case: continue_extra_max_usd 5, a cap of 10 crossed at 10.50, no amount named. One more
    cap would add 10, twice what a person may name; the default is held to the same most."""
    state = {**OVER, "pipeline_config": {"budget_usd": 10.0}, "total_cost_usd": 10.5, "budget_overrun_usd": 0.5}
    budget = BudgetSettings(continue_extra_max_usd=5.0)
    updates, summary, audits = _run(state, {"choice": "continue_retrying", "actor": "alex@example.com"}, budget)
    assert summary["budget"]["continue_adds"] == 5.0, summary["budget"]
    assert updates["budget_allowance_usd"] == pytest.approx(5.5), "spent 10.50 + 5 = a cap of 15.50 over a base of 10"
    assert audits[0][2]["cap_after_usd"] == 15.5
    assert continue_adds(state, ceiling=10.0, extra_max=5.0) == 5.0


def test_at_the_defaults_a_continue_still_adds_one_more_cap() -> None:
    state = {"pipeline_config": {"budget_usd": 10.0}, "total_cost_usd": 10.5}
    assert continue_adds(state, ceiling=10.0, extra_max=BudgetSettings().continue_extra_max_usd) == 10.0
