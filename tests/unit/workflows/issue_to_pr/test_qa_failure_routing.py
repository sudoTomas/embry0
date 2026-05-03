"""Phase 5 Task 6 — route_after_qa_report conditional dispatch.

After ``qa_report`` runs (in the issue→PR pipeline), the conditional edge
``route_after_qa_report`` reads the QA outcome and decides:

  - ``final_status="passed"``   → ``"end"``    (job is done, success)
  - ``final_status="exhausted"`` → ``"end"``    (qa internal exhaustion)
  - ``final_status="failed"`` AND ``failure_rounds < max_qa_failure_rounds``
        → ``"triage"``   (bounce back so triage can react with new context)
  - ``final_status="failed"`` AND ``failure_rounds >= max_qa_failure_rounds``
        → ``"exhausted"`` (graph maps to qa_exhausted node, ends with
          ``ERR_QA_FAILURES_UNRESOLVED``)

State convention (Phase 1): ``failure_rounds`` lives at
``state["qa"]["failure_rounds"]`` (nested), NOT top-level
``state["qa_failure_rounds"]``. The bookkeeping node bumps it BEFORE this
routing function fires, so by the time we look at it the count already
reflects the just-completed QA round.
"""

from __future__ import annotations

from athanor.workflows.issue_to_pr.routing import route_after_qa_report


def test_passed_routes_to_end() -> None:
    state = {"qa": {"final_status": "passed", "failure_rounds": 0}}
    assert route_after_qa_report(state) == "end"


def test_failed_routes_to_triage_under_cap() -> None:
    state = {"qa": {"final_status": "failed", "failure_rounds": 1}}
    assert route_after_qa_report(state) == "triage"


def test_failed_at_cap_routes_to_exhausted() -> None:
    """At/above the cap, route to 'exhausted' so the graph terminates with
    ERR_QA_FAILURES_UNRESOLVED instead of looping back to triage."""
    state = {"qa": {"final_status": "failed", "failure_rounds": 2}}
    assert route_after_qa_report(state) == "exhausted"


def test_exhausted_routes_to_end() -> None:
    """If QA's own retry loop exhausted (boot/seed/idle/etc.), don't bounce
    back to triage — surface the failure and end."""
    state = {"qa": {"final_status": "exhausted", "failure_rounds": 0}}
    assert route_after_qa_report(state) == "end"
