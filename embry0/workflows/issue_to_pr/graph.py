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

#298/#299: an APPROVED review does not dispatch the next template step
itself — it hands over to ``verify`` (no agent: diff tier classification +
the orchestrator-run base-vs-head test gate), which ends the job on new test
failures and otherwise makes the dispatch the diagram shows for review, with
the QA step pruned unless the change needs browser QA AND the repo's QA
config can boot the app from the branch. ``orchestrate_qa`` is the branch
variant: `target: deployed` apps are never QA'd by a branch job.

`triage_node`, `developer_node`, `review_node` and `verify_node` self-route via
`Command(goto=..., update=...)` for EVERY exit — none of them carries a
conditional edge (RAV-1152: a node with both Command returns and
conditional edges activates BOTH targets in one superstep). Their routers
(`check_triage_action`, `route_after_review`) are evaluated inside the
node tails for the single dispatch.

The QA subpath uses the multi-app orchestrator nodes:
``init_orchestrator_node`` loads qa.yaml v2 from a bootstrap sandbox,
``qa_orchestrator_node`` fans out one parallel sub-task per affected app,
and ``qa_report_node`` writes the sticky QA comment on the job's forge (a
GitHub issue comment or a GitLab issue note). Sub-tasks handle boot/QA failures internally and emit
``SubTaskResult`` with the right status; ``final_status`` is the gate.

After ``qa_report``, ``_qa_failure_bookkeeping_node`` increments
``state["qa"]["failure_rounds"]`` on ``failed`` outcomes (no-op for
``passed`` / ``exhausted``). The ``route_after_qa_report`` conditional
edge then dispatches to ``triage`` (failed under cap), ``qa_exhausted``
(failed AT cap → ``ERR_QA_FAILURES_UNRESOLVED``), or ``END``
(passed/exhausted).
"""

from typing import Any, cast

from langchain_core.runnables import RunnableConfig
from langgraph.graph import END, START, StateGraph

from embry0.orchestration.state import JobState
from embry0.safety.error_codes import ErrorCode
from embry0.workflows.issue_to_pr.generic_agent import (
    generic_agent_node,
    route_after_generic_agent,
)
from embry0.workflows.issue_to_pr.nodes import (
    ask_user_interrupt,
    developer_node,
    init_node,
    max_retries_node,
    retry_node,
    review_node,
    triage_node,
    verify_node,
)
from embry0.workflows.issue_to_pr.plan_route import finalize_output_node, plan_route_node
from embry0.workflows.issue_to_pr.route_plan import next_route
from embry0.workflows.issue_to_pr.routing import route_after_qa_report
from embry0.workflows.qa.orchestrator import (
    init_orchestrator_node,
    qa_orchestrator_node,
)
from embry0.workflows.qa.orchestrator_report import qa_report_node


async def _branch_qa_orchestrator_node(state: dict[str, Any], config: RunnableConfig) -> dict[str, Any]:
    """``qa_orchestrator_node`` for a branch job: deployed targets excluded (#298).

    The rule lives on the graph's own QA node, not on the routes into it, so
    it holds however QA was reached (verify, triage's rerun_qa, a template
    with no review step). Standalone QA (``QAWorkflow``) keeps deployed apps.
    """
    qa = {**(state.get("qa") or {}), "exclude_deployed_apps": True}
    # #468: the template's QA step config (model, tools, skills) reaches the
    # QA agent the way the developer's and reviewer's step configs reach
    # theirs. Sub-task states are built fresh per app, so it rides the
    # configurable the sub-tasks inherit; qa_node hands it to run_agent_node.
    from embry0.workflows.issue_to_pr.route_plan import step_template_config

    step_config = step_template_config(state, "qa")
    if step_config:
        configurable = {**((config or {}).get("configurable") or {}), "qa_template_config": step_config}
        config = cast(RunnableConfig, {**(config or {}), "configurable": configurable})
    return await qa_orchestrator_node({**state, "qa": qa}, config)


async def _qa_failure_bookkeeping_node(state: dict[str, Any]) -> dict[str, Any]:
    """Increment ``state["qa"]["failure_rounds"]`` when the just-completed
    QA round failed. No-op for ``passed`` / ``exhausted`` outcomes.

    Sits between ``qa_report`` and the ``route_after_qa_report`` conditional
    edge so the routing function can read an already-bumped count when
    deciding triage vs qa_exhausted. Keeping the increment in a dedicated
    node (rather than in ``qa_report`` or in the routing function) keeps
    ``qa_report`` reusable by the standalone QA workflow and keeps the
    routing function pure.

    RAV-1281: return ONLY the delta — this node used to return the whole
    state dict, and the ``operator.add`` reducers on ``agent_outputs`` /
    ``errors`` re-appended the entire accumulated history on every QA
    failure round (×2 per round: exponential checkpoint bloat inside the
    triage↔QA loop).
    """
    qa = state.get("qa") or {}
    if qa.get("final_status") == "failed":
        return {"qa": {**qa, "failure_rounds": qa.get("failure_rounds", 0) + 1}}
    if qa.get("final_status") == "passed" and state.get("route_plan"):
        # RAV-601: the qa template step is done — advance the cursor so
        # route_after_qa_report's next_route dispatch reads the post-QA
        # position (e.g. a trailing "output" step).
        from embry0.workflows.issue_to_pr.route_plan import advance

        return advance(state)
    return {}


async def _qa_exhausted_node(state: dict[str, Any]) -> dict[str, Any]:
    """Terminal node when ``failure_rounds`` hit ``max_qa_failure_rounds``.

    Sets ``final_status="exhausted"`` (overriding the prior ``failed`` so
    downstream observers see a single canonical end state) and writes
    ``error_code = ERR_QA_FAILURES_UNRESOLVED`` so the dashboard can bucket
    the failure cleanly.

    RAV-1281: delta return only — same reducer re-append hazard as
    ``_qa_failure_bookkeeping_node`` above.
    """
    qa = state.get("qa") or {}
    return {
        "qa": {**qa, "final_status": "exhausted"},
        "error_code": ErrorCode.QA_FAILURES_UNRESOLVED.value,
    }


class IssueToprWorkflow:
    name = "issue-to-pr"
    description = "Autonomous issue-to-PR pipeline: triage → develop → review → PR"

    def compile(self, config: Any = None) -> Any:
        builder = StateGraph(JobState)

        builder.add_node("init", init_node)  # type: ignore[type-var]
        # destinations= declares the Command-routing targets so the static
        # graph (introspection, viz, wiring tests) stays complete — the
        # nodes route dynamically via Command(goto=...) (RAV-1152).
        builder.add_node(
            "triage",
            triage_node,  # type: ignore[type-var]
            destinations=("plan_route", "developer", "max_retries", "ask_user_interrupt", "init_orchestrator", END),
        )
        # RAV-601: templates are interpreted over this fixed node superset —
        # plan_route snapshots the selected template into state and the
        # next_route dispatcher picks the next physical node at each seam.
        builder.add_node("plan_route", plan_route_node)  # type: ignore[type-var]
        builder.add_node("finalize_output", finalize_output_node)  # type: ignore[type-var]
        builder.add_node(
            "developer",
            developer_node,  # type: ignore[type-var]
            destinations=(
                "review",
                "max_retries",
                "ask_user_interrupt",
                "init_orchestrator",
                "finalize_output",
                "generic_agent",
                END,
            ),
        )
        builder.add_node(
            "review",
            review_node,  # type: ignore[type-var]
            destinations=(
                "retry",
                "max_retries",
                "ask_user_interrupt",
                "init_orchestrator",
                "finalize_output",
                "generic_agent",
                "review",
                "verify",
                END,
            ),
        )
        # #298/#299: deterministic verification after an approved review —
        # tier classification + the orchestrator-run base-vs-head test gate.
        # No agent. Self-routes via Command like its neighbours: END on new
        # test failures, else the route plan's next step.
        builder.add_node(
            "verify",
            verify_node,  # type: ignore[type-var]
            destinations=("retry", "init_orchestrator", "finalize_output", "generic_agent", "review", END),
        )
        # RAV-604: one physical node serves every non-code agent type
        # (research/analysis/ops) — it reads the current route step to know
        # which agent definition to run.
        builder.add_node("generic_agent", generic_agent_node)  # type: ignore[type-var]
        builder.add_node("retry", retry_node)  # type: ignore[type-var]
        builder.add_node("max_retries", max_retries_node)  # type: ignore[type-var]
        # RAV-1271: resumes route back to the ASKING node via Command —
        # a triage QA-escalation answer re-enters triage, a review question
        # answer re-enters review; zero-question arrivals fail to END.
        builder.add_node(
            "ask_user_interrupt",
            ask_user_interrupt,  # type: ignore[type-var]
            destinations=("triage", "developer", "review", END),
        )

        # Multi-app QA subpath nodes (Phase 1 C1 fix).
        builder.add_node("init_orchestrator", init_orchestrator_node)  # type: ignore[type-var]
        builder.add_node("orchestrate_qa", _branch_qa_orchestrator_node)  # type: ignore[type-var]
        builder.add_node("qa_report", qa_report_node)  # type: ignore[type-var]
        # Phase 5 Task 6: bookkeeping + exhaustion sink for the QA failure loop.
        builder.add_node("qa_failure_bookkeeping", _qa_failure_bookkeeping_node)
        builder.add_node("qa_exhausted", _qa_exhausted_node)

        builder.add_edge(START, "init")
        builder.add_edge("init", "triage")

        # RAV-1152: `triage` self-routes via Command EXCLUSIVELY (its tail
        # evaluates check_triage_action and dispatches). A node must never
        # carry BOTH Command returns and conditional edges — LangGraph
        # activates both targets in one superstep, fanning out (the
        # INVALID_CONCURRENT_GRAPH_UPDATE crash class before #95's reducers,
        # a spurious pause + duplicate node run after). See
        # test_mixed_command_and_conditional_edges_fan_out.

        # plan_route's update (route_plan + cursor 0) is merged before the
        # dispatcher runs, so next_route reads the fresh plan.
        builder.add_conditional_edges(
            "plan_route",
            next_route,
            {
                "developer": "developer",
                "review": "review",
                "qa": "init_orchestrator",
                "output": "finalize_output",
                "agent": "generic_agent",
                "end": END,
            },
        )
        builder.add_edge("finalize_output", END)

        # RAV-604: a generic step advanced the cursor on success — dispatch
        # the next template step (possibly another generic step); failures
        # terminate via route_after_generic_agent's "end".
        builder.add_conditional_edges(
            "generic_agent",
            route_after_generic_agent,
            {
                "developer": "developer",
                "review": "review",
                "qa": "init_orchestrator",
                "output": "finalize_output",
                "agent": "generic_agent",
                "end": END,
            },
        )

        # `developer` and `review` self-route via Command(goto=..., update=...)
        # returned from the node body — review's conditional edge is removed
        # (RAV-1152, same rationale as triage above; route_after_review is
        # now evaluated inside the node for the single dispatch).

        # Multi-app QA subpath wiring: init_orchestrator loads qa.yaml v2 from a
        # bootstrap sandbox, orchestrate_qa fans out one parallel sub-task per
        # affected app, qa_report writes the sticky QA comment on the job's
        # forge (GitHub issue comment or GitLab issue note). After qa_report, qa_failure_bookkeeping increments
        # failure_rounds + routes back to triage on failure (capped) or
        # terminates with ERR_QA_FAILURES_UNRESOLVED on exhaustion.
        builder.add_edge("init_orchestrator", "orchestrate_qa")
        builder.add_edge("orchestrate_qa", "qa_report")
        builder.add_edge("qa_report", "qa_failure_bookkeeping")
        builder.add_conditional_edges(
            "qa_failure_bookkeeping",
            route_after_qa_report,
            {
                "triage": "triage",
                "end": END,
                "exhausted": "qa_exhausted",
                # RAV-601: template steps may continue past a passed QA run.
                "developer": "developer",
                "review": "review",
                "qa": "init_orchestrator",
                "output": "finalize_output",
                "agent": "generic_agent",
            },
        )
        builder.add_edge("qa_exhausted", END)

        # After the user answers, ask_user_interrupt self-routes via Command
        # back to the asking node (triage / developer / review) so that agent
        # sees the Q&A in its rebuilt context (RAV-1271 — the old static edge
        # to developer lost triage- and review-asked answers).

        builder.add_edge("retry", "developer")

        # max_retries can loop back to developer (if user says continue) or end
        builder.add_conditional_edges(
            "max_retries",
            lambda state: "retry" if state.get("current_stage") == "developer_retry" else END,
            {"retry": "retry", END: END},
        )

        checkpointer = config.get("checkpointer") if isinstance(config, dict) else None
        return builder.compile(checkpointer=checkpointer)
