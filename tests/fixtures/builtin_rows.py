"""What a job of a built-in template records when plan_route plans it (#167), for tests that write jobs directly.

Progression and the world credit a job from what it recorded when it was
planned: ``jobs.coding_agent`` (the agent its route's coding step runs as)
and ``jobs.leader_agent`` (its template's leader). A test that inserts a
job of a built-in template, or builds its journal row, gives it these, as
a job planned since migration 143 has them (and an older one was given by
the migration).
"""

from __future__ import annotations

from typing import Any

from embry0.workflows.issue_to_pr.route_plan import build_route_plan, coding_agent_of
from embry0.workflows.row_behaviour import builtin_template, leader


def planned_credit(template: str | None) -> dict[str, Any]:
    """``coding_agent`` and ``leader_agent`` as plan_route records them for a job of the built-in ``template``
    (a pipeline the code defines records none of its own: plan_route never runs for it)."""
    row = builtin_template(template)
    graph = (row or {}).get("graph_definition")
    if not graph:
        return {"coding_agent": None, "leader_agent": None}
    return {"coding_agent": coding_agent_of(build_route_plan(graph)), "leader_agent": leader(row)}
