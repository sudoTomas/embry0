"""Issue-to-PR routing — workflow-specific conditional edges.

After Task 16, `developer_node` and `review_node` self-route via
`Command(goto=..., update=...)`. The previous `route_after_developer`
(budget check) and `route_after_review` (review decision) routers are
therefore obsolete and have been removed; their logic now lives inline
in the corresponding node bodies.

`route_after_triage` is still needed because triage routes statically
based on pipeline_config action (proceed vs split).
"""

from __future__ import annotations

from typing import Any, Literal

from athanor.orchestration.routing.conditions import check_triage_action


def route_after_triage(state: dict[str, Any]) -> Literal["proceed", "split"]:
    """Route after triage. needs_info handled by interrupt() inside the node."""
    return check_triage_action(state)
