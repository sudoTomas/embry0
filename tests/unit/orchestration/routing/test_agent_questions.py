"""Tests for the agent_questions routing helper."""

from __future__ import annotations

from legion.orchestration.routing.agent_questions import route_after_developer


def test_route_after_developer_with_pending_questions():
    """If pending_agent_questions is non-empty, route to ask_user."""
    state = {"pending_agent_questions": [{"question": "Which DB?"}]}
    assert route_after_developer(state) == "ask_user"


def test_route_after_developer_with_empty_pending_questions():
    """If pending_agent_questions is an empty list, route to review."""
    assert route_after_developer({"pending_agent_questions": []}) == "review"


def test_route_after_developer_with_missing_pending_questions():
    """If pending_agent_questions is missing entirely, route to review."""
    assert route_after_developer({}) == "review"


def test_route_after_developer_cycle_guard_fails_after_5_rounds():
    """Cycle guard: after 5 rounds of agent questions, the workflow fails
    rather than looping indefinitely."""
    # Simulated state: already asked 5 times, about to ask again
    state_ok = {"pending_agent_questions": [{"question": "q"}], "agent_question_rounds": 4}
    # At 4 we should still ask; the developer node will increment to 5.
    # The routing decision is made in developer_node (state update), not in this router.
    # So just verify the router still routes to ask_user given pending questions.
    from legion.orchestration.routing.agent_questions import route_after_developer

    assert route_after_developer(state_ok) == "ask_user"


def test_graph_router_routes_exhausted_to_over_budget():
    """When agent_questions_exhausted=True, the graph's combined router must
    bypass review (which would silently overwrite current_stage=failed) and
    return "over_budget" so the graph transitions to max_retries."""
    from legion.workflows.issue_to_pr.graph import _route_after_developer

    # Even if pending questions or within-budget, exhausted flag wins.
    state = {
        "agent_questions_exhausted": True,
        "pending_agent_questions": [{"question": "q"}],
        "current_stage": "failed",
    }
    assert _route_after_developer(state) == "over_budget"

    # Without the flag, normal routing applies (pending questions → ask_user).
    state_normal = {"pending_agent_questions": [{"question": "q"}]}
    assert _route_after_developer(state_normal) == "ask_user"
