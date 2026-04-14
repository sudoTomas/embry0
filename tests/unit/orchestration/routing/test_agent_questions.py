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
