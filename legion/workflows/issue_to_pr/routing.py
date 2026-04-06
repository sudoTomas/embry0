"""Issue-to-PR routing — workflow-specific conditional edges."""

from __future__ import annotations

from typing import Any, Literal

from legion.orchestration.routing.conditions import check_budget, check_review_decision, check_triage_action


def route_after_triage(state: dict[str, Any]) -> Literal["proceed", "split"]:
    """Route after triage. needs_info handled by interrupt() inside the node."""
    return check_triage_action(state)


def route_after_developer(state: dict[str, Any]) -> Literal["within_budget", "over_budget"]:
    """Route after developer. If over budget, go to max_retries (pause) instead of review."""
    return check_budget(state)


def route_after_review(state: dict[str, Any]) -> Literal["approved", "changes_requested", "max_retries"]:
    """Route after review based on structured decision."""
    return check_review_decision(state)
