"""Issue-to-PR workflow — LangGraph StateGraph definition."""

from typing import Any

from langgraph.graph import END, START, StateGraph

from legion.orchestration.state import JobState
from legion.workflows.issue_to_pr.nodes import (
    developer_node,
    git_ops_node,
    init_node,
    output_node,
    retry_developer_node,
    reviewer_node,
    triage_node,
    validator_node,
)
from legion.workflows.issue_to_pr.routing import (
    route_after_review,
    route_after_triage,
    route_after_validation,
)


class IssueToprWorkflow:
    name = "issue-to-pr"
    description = "Autonomous issue-to-PR pipeline: triage → develop → validate → review → PR"

    def compile(self, config: Any = None) -> Any:
        builder = StateGraph(JobState)

        builder.add_node("init", init_node)
        builder.add_node("triage", triage_node)
        builder.add_node("developer", developer_node)
        builder.add_node("validator", validator_node)
        builder.add_node("reviewer", reviewer_node)
        builder.add_node("git_ops", git_ops_node)
        builder.add_node("output", output_node)
        builder.add_node("retry_developer", retry_developer_node)

        builder.add_edge(START, "init")
        builder.add_edge("init", "triage")

        builder.add_conditional_edges(
            "triage",
            route_after_triage,
            {"proceed": "developer", "needs_info": "output", "split": "output"},
        )

        builder.add_edge("developer", "validator")

        builder.add_conditional_edges(
            "validator",
            route_after_validation,
            {"pass": "reviewer", "retry": "retry_developer"},
        )

        builder.add_edge("retry_developer", "developer")

        builder.add_conditional_edges(
            "reviewer",
            route_after_review,
            {"approved": "git_ops", "feedback": "retry_developer"},
        )

        builder.add_edge("git_ops", "output")
        builder.add_edge("output", END)

        return builder.compile()
