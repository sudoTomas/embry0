"""Routing for agent-initiated user questions.

When a developer (or other) agent emits `agent_ask_user` events during its
run, the enclosing node stores them as `pending_agent_questions` on state.
The conditional edge below diverts the graph to the `ask_user_interrupt`
node instead of continuing to the next stage.
"""

from __future__ import annotations

from typing import Any, Literal


def route_after_developer(state: dict[str, Any]) -> Literal["review", "ask_user"]:
    """Return "ask_user" if any pending agent questions are present, else "review"."""
    pending = state.get("pending_agent_questions")
    if pending:
        return "ask_user"
    return "review"
