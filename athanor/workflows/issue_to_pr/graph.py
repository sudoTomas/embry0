"""Issue-to-PR workflow — LangGraph StateGraph definition.

Graph (Phase 5 Task 6):

    init → triage → developer → [ask_user? | budget check] → review →
                  ↑     ↑           │                        │
                  │     │           └── ask_user_interrupt ──┤
                  │     └─── retry ←──────── (review_failed) ─┤
                  │                                           │
                  │      ┌─── (review_passed AND needs_qa) ───┤
                  │      │                                    │
                  │      ↓                                    │
                  │   init_qa → qa → qa_report                │
                  │                       │                   │
                  │                       ↓                   │
                  │              qa_failure_bookkeeping       │
                  │                       │                   │
                  │            ┌──────────┼──────────┐        │
                  │  (failed   │   (passed/         │ (failed │
                  │  AND under │   exhausted)       │  AT cap)│
                  │   cap)     │                    │         │
                  └───────── triage          END ←──┴── qa_exhausted → END
                                                                       │
                          (review_passed AND not needs_qa) → END ──────┘

`developer_node` self-routes via `Command(goto=..., update=...)` for every
exit. `review_node` keeps `Command(goto=...)` for the control-flow exits
(ask_user_interrupt, max_retries) but returns a plain dict (with
``current_stage`` set to ``review_passed`` / ``review_failed``) on the
happy path so the ``route_after_review`` conditional edge can dispatch
to the QA subpath when triage flagged ``needs_qa``.

The QA subpath reuses Phase 2's QA workflow nodes verbatim
(``init_qa_node``, ``qa_node``, ``report_node``). They are added as
inline nodes here (rather than invoking ``QAWorkflow().compile()`` as a
subgraph) so Task 6's ``route_after_qa_report`` can live in this graph
alongside the other issue→PR routing.

After ``qa_report``, ``_qa_failure_bookkeeping_node`` increments
``state["qa"]["failure_rounds"]`` on ``failed`` outcomes (no-op for
``passed`` / ``exhausted``). The ``route_after_qa_report`` conditional
edge then dispatches to ``triage`` (failed under cap), ``qa_exhausted``
(failed AT cap → ``ERR_QA_FAILURES_UNRESOLVED``), or ``END``
(passed/exhausted).
"""

from typing import Any

from langgraph.graph import END, START, StateGraph

from athanor.orchestration.state import JobState
from athanor.safety.error_codes import ErrorCode
from athanor.workflows.issue_to_pr.nodes import (
    ask_user_interrupt,
    developer_node,
    init_node,
    max_retries_node,
    retry_node,
    review_node,
    triage_node,
)
from athanor.workflows.issue_to_pr.routing import (
    route_after_qa_report,
    route_after_review,
    route_after_triage,
)
from athanor.workflows.qa.nodes import boot_qa_node, init_qa_node, qa_node, report_node


async def _qa_failure_bookkeeping_node(state: dict[str, Any]) -> dict[str, Any]:
    """Increment ``state["qa"]["failure_rounds"]`` when the just-completed
    QA round failed. No-op for ``passed`` / ``exhausted`` outcomes.

    Sits between ``qa_report`` and the ``route_after_qa_report`` conditional
    edge so the routing function can read an already-bumped count when
    deciding triage vs qa_exhausted. Keeping the increment in a dedicated
    node (rather than in ``qa_report`` or in the routing function) keeps
    ``qa_report`` reusable by the standalone QA workflow and keeps the
    routing function pure.
    """
    qa = state.get("qa") or {}
    if qa.get("final_status") == "failed":
        qa["failure_rounds"] = qa.get("failure_rounds", 0) + 1
        state["qa"] = qa
    return state


async def _qa_exhausted_node(state: dict[str, Any]) -> dict[str, Any]:
    """Terminal node when ``failure_rounds`` hit ``max_qa_failure_rounds``.

    Sets ``final_status="exhausted"`` (overriding the prior ``failed`` so
    downstream observers see a single canonical end state) and writes
    ``error_code = ERR_QA_FAILURES_UNRESOLVED`` so the dashboard can bucket
    the failure cleanly.
    """
    qa = state.get("qa") or {}
    qa["final_status"] = "exhausted"
    state["qa"] = qa
    state["error_code"] = ErrorCode.QA_FAILURES_UNRESOLVED.value
    return state


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
        # Backend-owned boot phase between init_qa and qa (qa-boot-as-backend-node plan).
        builder.add_node("boot_qa", boot_qa_node)  # type: ignore[type-var]
        builder.add_node("qa", qa_node)  # type: ignore[type-var]
        builder.add_node("qa_report", report_node)  # type: ignore[type-var]
        # Phase 5 Task 6: bookkeeping + exhaustion sink for the QA failure loop.
        builder.add_node("qa_failure_bookkeeping", _qa_failure_bookkeeping_node)
        builder.add_node("qa_exhausted", _qa_exhausted_node)

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

        # QA subpath wiring. init_qa → boot_qa → qa → qa_report mirrors the
        # standalone QAWorkflow (athanor/workflows/qa/graph.py). boot_qa
        # Command-routes to qa (success) or qa_report (timeout/startup_failed).
        # Phase 5 Task 6 adds a bookkeeping node + conditional edge AFTER
        # qa_report that bounces back to triage on QA failure (capped) or
        # terminates with ERR_QA_FAILURES_UNRESOLVED on exhaustion.
        builder.add_edge("init_qa", "boot_qa")
        # boot_qa → {qa, qa_report} via Command(goto=...) — no static edge.
        builder.add_edge("qa", "qa_report")
        builder.add_edge("qa_report", "qa_failure_bookkeeping")
        builder.add_conditional_edges(
            "qa_failure_bookkeeping",
            route_after_qa_report,
            {"triage": "triage", "end": END, "exhausted": "qa_exhausted"},
        )
        builder.add_edge("qa_exhausted", END)

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
