"""Issue-to-PR workflow — LangGraph StateGraph definition.

Graph (Phase 5 Task 5):

    init → triage → developer → [ask_user? | budget check] → review →
                        ↑             │                        │
                        │             └── ask_user_interrupt ──┤
                        └────────── retry ←──────── (review_failed) ─┤
                                                                     │
                          ┌─── (review_passed AND needs_qa) ─────────┤
                          │                                          │
                          ↓                                          │
                       init_qa → qa → qa_report → END                │
                                                                     │
                          (review_passed AND not needs_qa) → END ────┘

`developer_node` self-routes via `Command(goto=..., update=...)` for every
exit. `review_node` keeps `Command(goto=...)` for the control-flow exits
(ask_user_interrupt, max_retries) but returns a plain dict (with
``current_stage`` set to ``review_passed`` / ``review_failed``) on the
happy path so the ``route_after_review`` conditional edge can dispatch
to the QA subpath when triage flagged ``needs_qa``.

The QA subpath reuses Phase 2's QA workflow nodes verbatim
(``init_qa_node``, ``qa_node``, ``report_node``). They are added as
inline nodes here (rather than invoking ``QAWorkflow().compile()`` as a
subgraph) so Task 6's ``route_after_report`` can live in this graph
alongside the other issue→PR routing.
"""

from typing import Any

from langgraph.graph import END, START, StateGraph

from athanor.orchestration.state import JobState
from athanor.workflows.issue_to_pr.nodes import (
    ask_user_interrupt,
    developer_node,
    init_node,
    max_retries_node,
    retry_node,
    review_node,
    triage_node,
)
from athanor.workflows.issue_to_pr.routing import route_after_review, route_after_triage
from athanor.workflows.qa.nodes import init_qa_node, qa_node, report_node


class IssueToprWorkflow:
    name = "issue-to-pr"
    description = "Autonomous issue-to-PR pipeline: triage → develop → review → PR"

    def compile(self, config: Any = None) -> Any:
        builder = StateGraph(JobState)

        builder.add_node("init", init_node)  # type: ignore[type-var]
        builder.add_node("triage", triage_node)  # type: ignore[type-var]
        builder.add_node("developer", developer_node)  # type: ignore[type-var]
        builder.add_node("review", review_node)  # type: ignore[type-var]
        builder.add_node("retry", retry_node)  # type: ignore[type-var]
        builder.add_node("max_retries", max_retries_node)  # type: ignore[type-var]
        builder.add_node("ask_user_interrupt", ask_user_interrupt)  # type: ignore[type-var]

        # Phase 5 Task 5: QA subpath nodes, reused verbatim from Phase 2.
        builder.add_node("init_qa", init_qa_node)  # type: ignore[type-var]
        builder.add_node("qa", qa_node)  # type: ignore[type-var]
        builder.add_node("qa_report", report_node)  # type: ignore[type-var]

        builder.add_edge(START, "init")
        builder.add_edge("init", "triage")

        builder.add_conditional_edges(
            "triage",
            route_after_triage,
            {"proceed": "developer", "split": END},
        )

        # `developer` self-routes via Command(goto=..., update=...) returned
        # from the node body. `review` self-routes for control-flow exits but
        # returns a plain dict on approved/changes_requested so the
        # conditional edge below can dispatch to the QA subpath.
        builder.add_conditional_edges(
            "review",
            route_after_review,
            {"developer": "retry", "qa": "init_qa", "end": END},
        )

        # QA subpath wiring. init_qa → qa → qa_report is identical to the
        # standalone QAWorkflow (athanor/workflows/qa/graph.py). Routing OUT
        # of qa_report is stubbed to END here; Phase 5 Task 6 replaces this
        # with a conditional edge that bounces back to triage on QA failure.
        builder.add_edge("init_qa", "qa")
        builder.add_edge("qa", "qa_report")
        builder.add_edge("qa_report", END)

        # After the user answers, re-run the developer so the agent sees the
        # Q&A in its rebuilt context.
        builder.add_edge("ask_user_interrupt", "developer")

        builder.add_edge("retry", "developer")

        # max_retries can loop back to developer (if user says continue) or end
        builder.add_conditional_edges(
            "max_retries",
            lambda state: "retry" if state.get("current_stage") == "developer_retry" else END,
            {"retry": "retry", END: END},
        )

        checkpointer = config.get("checkpointer") if isinstance(config, dict) else None
        return builder.compile(checkpointer=checkpointer)
