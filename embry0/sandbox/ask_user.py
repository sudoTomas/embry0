"""Agent-facing helper to request input from the user mid-execution.

Usage inside agent code (which runs in the sandbox):

    from embry0.sandbox.ask_user import ask_user

    ask_user(
        "Should I use PostgreSQL or MySQL for the new feature?",
        category="design",
        options=["PostgreSQL", "MySQL"],
    )

The helper writes a structured event to stdout which the orchestrator reads
via the sandbox runner's event stream. The agent does NOT block — it continues
execution and the orchestrator decides (based on the event) to pause the
pipeline after the agent finishes. When the user answers, the job is re-invoked
from the last checkpoint with the Q&A pairs included in the agent's context.
"""

from __future__ import annotations

from embry0.sandbox.events import EventType, emit_event


def ask_user(
    question: str,
    category: str | None = None,
    options: list[str] | None = None,
    *,
    importance: str = "blocking",
    suggested_answer: str | None = None,
) -> None:
    """Emit an agent_ask_user event — the pipeline will pause after the agent
    exits and wait for the user to respond via the dashboard.

    Doesn't block. When the user answers, the job is re-invoked from the last
    checkpoint with the Q&A pairs included in the agent's context.

    ``importance`` is either ``"blocking"`` (default) or ``"auto_answerable"``.
    When ``importance="auto_answerable"`` the orchestrator records
    ``suggested_answer`` as the answer and continues the workflow without
    pausing — the user can override post-hoc from the dashboard.
    """
    kwargs: dict[str, object] = {
        "question": question,
        "category": category or "general",
        "options": list(options) if options else [],
        "importance": importance,
    }
    if importance == "auto_answerable" and suggested_answer is not None:
        kwargs["suggested_answer"] = suggested_answer
    emit_event(EventType.AGENT_ASK_USER, **kwargs)
