"""Issue-to-PR routing — workflow-specific conditional edges.

After Task 16, `developer_node` and `review_node` self-route via
`Command(goto=..., update=...)` for the special control-flow paths
(ask_user_interrupt, max_retries). The "happy path" exits from
`review_node` (approved / changes_requested) instead set
``current_stage`` and return a plain dict, letting the conditional
edge ``route_after_review`` dispatch to:

  - ``"developer"`` (review_failed → existing retry → developer chain)
  - ``"qa"``        (review_passed AND triage flagged needs_qa)
  - ``"end"``       (review_passed AND no QA required)

The mapping in ``graph.py`` resolves ``"developer"`` to the existing
``retry`` node so the feedback-injection step is preserved.

`route_after_triage` is still needed because triage routes statically
based on pipeline_config action (proceed vs split).
"""

from __future__ import annotations

from typing import Any, Literal

from athanor.orchestration.routing.conditions import check_triage_action


def route_after_triage(state: dict[str, Any]) -> Literal["proceed", "split"]:
    """Route after triage. needs_info handled by interrupt() inside the node."""
    return check_triage_action(state)


def route_after_review(state: dict[str, Any]) -> Literal["developer", "qa", "end"]:
    """Decide the next node after a successful review.

    - ``review_failed`` → ``developer`` (resolved to ``retry`` by the
      conditional edge mapping; preserves the existing review-fail loop).
    - Otherwise, when triage set ``state["qa"]["needs_qa"] = True``, route
      to the QA subpath (``qa`` → mapped to ``init_qa``).
    - Otherwise terminate the graph (``end`` → ``END``).

    Phase 1 fix: ``needs_qa`` lives on the nested QA state block at
    ``state["qa"]["needs_qa"]``, not at the top level. Absence of either
    the ``qa`` block or the ``needs_qa`` key is treated as ``False``.
    """
    if state.get("current_stage") == "review_failed":
        return "developer"
    qa_block = state.get("qa") or {}
    if qa_block.get("needs_qa", False):
        return "qa"
    return "end"
