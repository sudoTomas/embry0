"""pr-review pipeline — LangGraph StateGraph (#30).

init_pr_review → review → publish → cleanup → END

Every edge is unconditional: a failed node marks ``pr_review.final_status``
and the later nodes do nothing, so cleanup (sandbox destroy) always runs.
"""

from typing import Any

from langgraph.graph import END, START, StateGraph

from embry0.orchestration.state import JobState


class PrReviewWorkflow:
    name = "pr-review"
    description = "Review a pull request or merge request at its head commit and post the findings on it"

    def compile(self, config: Any = None) -> Any:
        from embry0.workflows.pr_review.nodes import cleanup_node, init_pr_review_node, publish_node, review_node

        builder = StateGraph(JobState)
        builder.add_node("init_pr_review", init_pr_review_node)  # type: ignore[type-var]
        builder.add_node("review", review_node)  # type: ignore[type-var]
        builder.add_node("publish", publish_node)  # type: ignore[type-var]
        builder.add_node("cleanup", cleanup_node)  # type: ignore[type-var]
        builder.add_edge(START, "init_pr_review")
        builder.add_edge("init_pr_review", "review")
        builder.add_edge("review", "publish")
        builder.add_edge("publish", "cleanup")
        builder.add_edge("cleanup", END)

        checkpointer = config.get("checkpointer") if isinstance(config, dict) else None
        return builder.compile(checkpointer=checkpointer)
