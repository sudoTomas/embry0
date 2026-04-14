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
