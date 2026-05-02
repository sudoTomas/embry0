"""Phase 5 Task 5 — route_after_review conditional dispatch.

After review_node sets current_stage to "review_passed" or "review_failed"
(plain-dict return path; the special max_retries / ask_user_interrupt paths
keep Command(goto=...)), the conditional edge routes to:
  - "developer" (retry) on review_failed
  - "qa"        when triage flagged needs_qa
  - "end"       otherwise

State convention (Phase 1 fix): needs_qa lives at state["qa"]["needs_qa"],
NOT top-level state["needs_qa"].
"""

from __future__ import annotations

from athanor.workflows.issue_to_pr.routing import route_after_review


def test_route_to_qa_when_needs_qa_true() -> None:
    state = {"qa": {"needs_qa": True}, "current_stage": "review_passed"}
    assert route_after_review(state) == "qa"


def test_route_to_end_when_needs_qa_false() -> None:
    state = {"qa": {"needs_qa": False}, "current_stage": "review_passed"}
    assert route_after_review(state) == "end"


def test_route_to_end_when_qa_block_missing() -> None:
    """Absent qa block → treat as needs_qa=False."""
    state = {"current_stage": "review_passed"}
    assert route_after_review(state) == "end"


def test_route_to_developer_on_review_failure() -> None:
    """Review-fail flow is preserved: route to 'developer' (mapped to the
    existing retry node by add_conditional_edges in graph.py) regardless
    of needs_qa."""
    state = {"qa": {"needs_qa": True}, "current_stage": "review_failed"}
    assert route_after_review(state) == "developer"
