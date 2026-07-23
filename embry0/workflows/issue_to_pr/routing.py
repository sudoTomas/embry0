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

from embry0.orchestration.routing.conditions import check_triage_action


def route_after_triage(state: dict[str, Any]) -> Literal["proceed", "split"]:
    """Route after triage. needs_info handled by interrupt() inside the node."""
    return check_triage_action(state)


def route_after_review(state: dict[str, Any]) -> Literal["developer", "qa", "output", "end"]:
    """Decide the next node after a successful review.

    - ``review_failed`` → ``developer`` (resolved to ``retry`` by the
      conditional edge mapping; preserves the existing review-fail loop —
      an internal loop, not a template edge, so the cursor is untouched).
    - Otherwise the route plan decides (RAV-601): review_node already
      advanced the cursor on approval, so ``next_route`` reads the
      post-review position. Jobs without a plan (pre-RAV-601 checkpoints)
      keep the legacy needs_qa/end behavior.

    Phase 1 fix: ``needs_qa`` lives on the nested QA state block at
    ``state["qa"]["needs_qa"]``, not at the top level. Absence of either
    the ``qa`` block or the ``needs_qa`` key is treated as ``False``.
    """
    if state.get("current_stage") == "review_failed":
        return "developer"
    if state.get("route_plan"):
        from embry0.workflows.issue_to_pr.route_plan import next_route

        target = next_route(state)
        return "end" if target == "end" else target  # type: ignore[return-value]
    qa_block = state.get("qa") or {}
    if qa_block.get("needs_qa", False):
        return "qa"
    return "end"


# Default cap on triage↔QA bounces before we give up. Mirrors the docstring
# in QAStateBlock.failure_rounds. Overridable per-job via
# ``state["qa"]["max_qa_failure_rounds"]``.
DEFAULT_MAX_QA_FAILURE_ROUNDS = 2


def route_after_qa_report(
    state: dict[str, Any],
) -> Literal["triage", "end", "exhausted", "developer", "review", "qa", "output"]:
    """Decide the next node after ``qa_report`` in the issue→PR pipeline.

    Reads ``state["qa"]["final_status"]``:

    - ``"passed"``    → the route plan's next step (RAV-601; the
      bookkeeping node advanced the cursor on pass) — ``"end"`` when the
      plan is exhausted or absent
    - ``"exhausted"`` → ``"end"``  (QA's internal retry loop gave up; don't
      bounce back to triage, surface the failure)
    - ``"failed"``    → ``"triage"`` if ``state["qa"]["failure_rounds"]`` is
      strictly less than ``state["qa"]["max_qa_failure_rounds"]`` (default
      :data:`DEFAULT_MAX_QA_FAILURE_ROUNDS`), else ``"exhausted"`` so the
      graph terminates with ``ERR_QA_FAILURES_UNRESOLVED``.
    - anything else (e.g. ``"pending"``, missing) → ``"end"`` defensively;
      this should never happen at this stage in practice.

    Phase 1 convention: ``failure_rounds`` lives on the nested QA state
    block (``state["qa"]["failure_rounds"]``), NOT at the top level. The
    ``_qa_failure_bookkeeping_node`` increments it BEFORE this routing
    function fires, so the value already reflects the just-completed round.

    Pure: no state mutation, no I/O. Side effects (the increment, error
    code writes) live in dedicated nodes.
    """
    qa = state.get("qa") or {}
    status = qa.get("final_status", "pending")
    rounds = qa.get("failure_rounds", 0)
    max_rounds = qa.get("max_qa_failure_rounds", DEFAULT_MAX_QA_FAILURE_ROUNDS)

    if status == "passed":
        if state.get("route_plan"):
            from embry0.workflows.issue_to_pr.route_plan import next_route

            target = next_route(state)
            return "end" if target == "end" else target  # type: ignore[return-value]
        return "end"
    if status == "exhausted":
        return "end"
    if status == "failed":
        if rounds >= max_rounds:
            return "exhausted"
        return "triage"
    return "end"
