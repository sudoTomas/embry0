"""Issue-to-PR workflow — LangGraph StateGraph definition.

Graph: init → triage → developer → [ask_user? | budget check] → review → END
                            ↑             │                        │
                            │             └── ask_user_interrupt ──┤
                            └────────── retry ←────────────────────┘
                                         (up to 3x, then max_retries interrupt)
"""

from typing import Any, Literal

from langgraph.graph import END, START, StateGraph

from legion.orchestration.routing.agent_questions import (
    route_after_developer as route_for_agent_questions,
)
from legion.orchestration.state import JobState
from legion.workflows.issue_to_pr.nodes import (
    ask_user_interrupt,
    developer_node,
    init_node,
    max_retries_node,
    retry_node,
    review_node,
    triage_node,
)
from legion.workflows.issue_to_pr.routing import (
    route_after_developer as route_after_developer_budget,
)
from legion.workflows.issue_to_pr.routing import (
    route_after_review,
    route_after_triage,
)


def _route_after_developer(state: dict[str, Any]) -> Literal["ask_user", "within_budget", "over_budget"]:
    """Combined router after the developer node.

    Priority:
    1. If the agent asked the user questions, divert to ask_user_interrupt.
    2. Otherwise, apply the budget check (within_budget → review, over_budget → max_retries).
    """
    if route_for_agent_questions(state) == "ask_user":
        return "ask_user"
    return route_after_developer_budget(state)


class IssueToprWorkflow:
    name = "issue-to-pr"
    description = "Autonomous issue-to-PR pipeline: triage → develop → review → PR"

    def compile(self, config: Any = None) -> Any:
        builder = StateGraph(JobState)

        builder.add_node("init", init_node)
        builder.add_node("triage", triage_node)
        builder.add_node("developer", developer_node)
        builder.add_node("review", review_node)
        builder.add_node("retry", retry_node)
        builder.add_node("max_retries", max_retries_node)
        builder.add_node("ask_user_interrupt", ask_user_interrupt)

        builder.add_edge(START, "init")
        builder.add_edge("init", "triage")

        builder.add_conditional_edges(
            "triage",
            route_after_triage,
            {"proceed": "developer", "split": END},
        )

        # After the developer node: either the agent paused to ask the user
        # (ask_user_interrupt), or we apply the existing budget check and
        # continue to review / max_retries.
        builder.add_conditional_edges(
            "developer",
            _route_after_developer,
            {
                "ask_user": "ask_user_interrupt",
                "within_budget": "review",
                "over_budget": "max_retries",
            },
        )

        # After the user answers, re-run the developer so the agent sees the
        # Q&A in its rebuilt context.
        builder.add_edge("ask_user_interrupt", "developer")

        builder.add_conditional_edges(
            "review",
            route_after_review,
            {"approved": END, "changes_requested": "retry", "max_retries": "max_retries"},
        )

        builder.add_edge("retry", "developer")

        # max_retries can loop back to developer (if user says continue) or end
        builder.add_conditional_edges(
            "max_retries",
            lambda state: "retry" if state.get("current_stage") == "developer_retry" else END,
            {"retry": "retry", END: END},
        )

        checkpointer = config.get("checkpointer") if isinstance(config, dict) else None
        return builder.compile(checkpointer=checkpointer)
