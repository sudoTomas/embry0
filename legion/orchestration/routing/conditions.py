"""Conditional edge functions for LangGraph graph routing."""

from typing import Any, Literal


def check_triage_action(state: dict[str, Any]) -> Literal["proceed", "needs_info", "split"]:
    config = state.get("pipeline_config", {})
    action = config.get("action", "proceed")
    if action in ("proceed", "needs_info", "split"):
        return action
    return "proceed"


def check_validation_result(state: dict[str, Any]) -> Literal["pass", "fail"]:
    result = state.get("validation_result")
    if result and result.get("passed"):
        return "pass"
    return "fail"


def check_review_result(state: dict[str, Any]) -> Literal["approved", "rejected"]:
    outputs = state.get("agent_outputs", [])
    reviewer_outputs = [o for o in outputs if o.get("agent_type") == "reviewer"]
    if not reviewer_outputs:
        return "rejected"
    latest = reviewer_outputs[-1]
    output_text = (latest.get("output") or "").upper()
    if "APPROVED" in output_text:
        return "approved"
    return "rejected"


def check_budget(state: dict[str, Any]) -> Literal["within_budget", "over_budget"]:
    cost = state.get("total_cost_usd", 0.0)
    config = state.get("pipeline_config", {})
    pipeline = config.get("pipeline_config", {})
    max_budget = pipeline.get("budget_usd", 10.0)
    if cost > max_budget:
        return "over_budget"
    return "within_budget"
