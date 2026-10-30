"""A job paused by the release before resumes on this one with the budget it had (#171 review).

The release before had no ``budget_cap_usd`` / ``budget_spent_usd`` in the
job state. A reducer channel over a plain ``float`` starts at 0.0, so a job it
checkpointed would resume here with a recorded cap of 0: soft-mode routing
would read it as over budget, and a person's ``continue_retrying`` would grant
an allowance and write a ``job.budget_raised`` row the release before never
would. The keys are optional instead, and routing trusts a recorded cap only
once a node has recorded one.

The real issue-to-PR graph runs to a pause on the release before's state
schema (``JobState`` without the two keys, the node not writing them), then is
compiled with today's ``JobState`` over the same checkpointer and resumed, as
an orchestrator restarted on the new release does.
"""

from __future__ import annotations

import os
from typing import Any, TypedDict, get_type_hints
from unittest.mock import patch

import pytest
from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END
from langgraph.types import Command

import embry0.workflows.issue_to_pr.graph as graph_mod
from embry0.config import Embry0Config
from embry0.execution.agent_runner import AgentOutput
from embry0.orchestration.nodes.agent import run_agent_node
from embry0.orchestration.routing.conditions import check_budget
from embry0.orchestration.state import JobState
from embry0.services.budget_policy import BudgetPolicy
from embry0.settings.bootstrap import import_env_once
from embry0.settings.earlier import EARLIER_SOURCES
from embry0.settings.store import SettingsStore
from embry0.storage.database import DatabasePool

NEW_KEYS = ("budget_cap_usd", "budget_spent_usd")


class _Runner:
    async def run(self, **kwargs: Any) -> AgentOutput:
        return AgentOutput(agent_type="developer", output="done", cost_usd=1.0)


def _release_before_schema() -> Any:
    """``JobState`` as the release before declared it: without the two keys."""
    hints = get_type_hints(JobState, include_extras=True)
    return TypedDict("ReleaseBeforeJobState", {k: v for k, v in hints.items() if k not in NEW_KEYS}, total=False)  # type: ignore[misc]


@pytest.mark.asyncio
async def test_a_job_paused_before_the_upgrade_resumes_with_no_allowance_it_did_not_have(
    db_with_migrations: DatabasePool,
) -> None:
    db = db_with_migrations
    with patch.dict(os.environ, {}, clear=True):
        config = Embry0Config(_env_file=None)  # type: ignore[call-arg]
    store = SettingsStore(db)
    await import_env_once(store, config, earlier=EARLIER_SOURCES)
    policy = BudgetPolicy(store, config, earlier=EARLIER_SOURCES)

    release_before = {"on": True}
    reviews = {"n": 0}

    async def init(state: dict[str, Any]) -> dict[str, Any]:
        return {"current_stage": "init"}

    async def triage(state: dict[str, Any]) -> Any:
        return Command(goto="developer", update={"pipeline_config": {"budget_usd": 10.0}})

    async def developer(state: dict[str, Any], config: RunnableConfig) -> Any:
        updates = await run_agent_node(
            state={"sandbox_container_id": "c1", **state},
            agent_runner=_Runner(),
            agent_type="developer",
            prompt="implement",
            credentials={"api_key": "", "oauth_token": "oauth-xyz"},
            config=config,
        )
        if release_before["on"]:
            # The release before's node did not record the cap or the spend.
            for key in NEW_KEYS:
                updates.pop(key, None)
        if check_budget({**state, **updates}) == "over_budget":
            return Command(goto="max_retries", update=updates)
        return Command(goto="review", update=updates)

    async def review(state: dict[str, Any]) -> Any:
        reviews["n"] += 1
        # The third rejected review gives up and asks the person: a pause that is not about the budget.
        if reviews["n"] == 3:
            return Command(goto="max_retries", update={"current_stage": "review_failed", "retry_count": 3})
        if reviews["n"] >= 8:
            return Command(goto=END, update={"current_stage": "done"})
        return Command(goto="developer", update={"current_stage": "review"})

    saver = MemorySaver()
    cfg: Any = {
        "configurable": {"thread_id": "t", "job_id": "J", "db": db, "embry0_config": config, "budget_policy": policy},
        "recursion_limit": 500,
    }

    def compiled(schema: Any) -> Any:
        with (
            patch.object(graph_mod, "init_node", init),
            patch.object(graph_mod, "triage_node", triage),
            patch.object(graph_mod, "developer_node", developer),
            patch.object(graph_mod, "review_node", review),
            patch.object(graph_mod, "JobState", schema),
        ):
            return graph_mod.IssueToprWorkflow().compile({"checkpointer": saver})

    before = compiled(_release_before_schema())
    await before.ainvoke({"job_id": "J", "total_cost_usd": 0.0}, config=cfg)
    paused = await before.aget_state(cfg)
    assert paused.tasks and paused.tasks[0].interrupts, "the job paused for a person on the release before"
    assert paused.values["total_cost_usd"] == pytest.approx(3.0), "three agent turns at $1, under the cap of 10"

    release_before["on"] = False
    after = compiled(JobState)
    resumed = await after.aget_state(cfg)
    recorded = resumed.values.get("budget_cap_usd")
    assert check_budget(dict(resumed.values)) == "within_budget", (
        f"$3 spent against a cap of 10 is within budget, as the release before routed it; "
        f"the job resumed with a recorded cap of {recorded!r}"
    )

    await after.ainvoke(Command(resume={"choice": "continue_retrying", "actor": "alex@example.com"}), config=cfg)
    final = await after.aget_state(cfg)
    assert final.values["current_stage"] == "done"
    rows = await db.fetch("SELECT details FROM audit_log WHERE action = 'job.budget_raised'")
    assert [dict(r["details"]) for r in rows] == [], "no job.budget_raised row for a cap nobody raised"
    assert not final.values.get("budget_allowance_usd"), (
        f"a continue from a pause that was not over budget grants no allowance: "
        f"{final.values.get('budget_allowance_usd')}"
    )
    assert recorded is None, f"a job the release before checkpointed has no recorded cap, not {recorded!r}"
    assert final.values.get("budget_cap_usd") == pytest.approx(10.0), "today's node records the cap from then on"
