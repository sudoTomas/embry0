"""Issue-to-PR workflow — LangGraph StateGraph definition.

Graph (Phase 5 Task 6 / Phase 1 C1 fix):

    init → triage → developer → [ask_user? | budget check] → review →
                  ↑     ↑           │                        │
                  │     │           └── ask_user_interrupt ──┤
                  │     └─── retry ←──────── (review_failed) ─┤
                  │                                           │
                  │      ┌─── (review_passed AND needs_qa) ───┤
                  │      │                                    │
                  │      ↓                                    │
                  │   init_orchestrator → orchestrate_qa → qa_report
                  │                                           │
                  │                                           ↓
                  │                              qa_failure_bookkeeping
                  │                                           │
                  │                           ┌──────────────┼──────────┐
                  │  (failed AND under cap)   │  (passed/    │  (failed │
                  │                           │  exhausted)  │  AT cap) │
                  └───────── triage          END ←───────────┴── qa_exhausted → END
                                                                       │
                          (review_passed AND not needs_qa) → END ──────┘

`developer_node` self-routes via `Command(goto=..., update=...)` for every
exit. `review_node` keeps `Command(goto=...)` for the control-flow exits
(ask_user_interrupt, max_retries) but returns a plain dict (with
``current_stage`` set to ``review_passed`` / ``review_failed``) on the
happy path so the ``route_after_review`` conditional edge can dispatch
to the QA subpath when triage flagged ``needs_qa``.

The QA subpath uses the multi-app orchestrator nodes:
``init_orchestrator_node`` loads qa.yaml v2 from a bootstrap sandbox,
``qa_orchestrator_node`` fans out one parallel sub-task per affected app,
and ``qa_report_node`` writes the aggregate GitHub check + sticky PR
comment. Sub-tasks handle boot/QA failures internally and emit
``SubTaskResult`` with the right status; ``final_status`` is the gate.

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
from athanor.workflows.qa.orchestrator import (
    init_orchestrator_node,
    qa_orchestrator_node,
)
from athanor.workflows.qa.orchestrator_report import qa_report_node


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

        # Multi-app QA subpath nodes (Phase 1 C1 fix).
        builder.add_node("init_orchestrator", init_orchestrator_node)  # type: ignore[type-var]
        builder.add_node("orchestrate_qa", qa_orchestrator_node)  # type: ignore[type-var]
        builder.add_node("qa_report", qa_report_node)  # type: ignore[type-var]
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
            {"developer": "retry", "qa": "init_orchestrator", "end": END},
        )

        # Multi-app QA subpath wiring: init_orchestrator loads qa.yaml v2 from a
        # bootstrap sandbox, orchestrate_qa fans out one parallel sub-task per
        # affected app, qa_report writes the aggregate GitHub check + sticky PR
        # comment. After qa_report, qa_failure_bookkeeping increments
        # failure_rounds + routes back to triage on failure (capped) or
        # terminates with ERR_QA_FAILURES_UNRESOLVED on exhaustion.
        builder.add_edge("init_orchestrator", "orchestrate_qa")
        builder.add_edge("orchestrate_qa", "qa_report")
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
