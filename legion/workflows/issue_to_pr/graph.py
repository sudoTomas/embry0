"""Issue-to-PR workflow — LangGraph StateGraph definition.

Graph: init → triage → developer → review → END
                            ↑           |
                            └── retry ←──┘ (up to 3x, then max_retries interrupt)
"""

from typing import Any

from langgraph.graph import END, START, StateGraph

from legion.orchestration.state import JobState
from legion.workflows.issue_to_pr.nodes import (
    developer_node,
    init_node,
    max_retries_node,
    retry_node,
    review_node,
    triage_node,
)
from legion.workflows.issue_to_pr.routing import (
    route_after_review,
    route_after_triage,
)


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

        builder.add_edge(START, "init")
        builder.add_edge("init", "triage")

        builder.add_conditional_edges(
            "triage",
            route_after_triage,
            {"proceed": "developer", "split": END},
        )

        builder.add_edge("developer", "review")

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
