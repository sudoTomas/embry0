"""Issue-to-PR routing — workflow-specific conditional edges."""

from typing import Any, Literal

from legion.orchestration.routing.conditions import check_awaiting_input, check_triage_action, check_validation_result


def route_after_triage(state: dict[str, Any]) -> Literal["proceed", "needs_info", "split", "awaiting_input"]:
    if check_awaiting_input(state):
        return "awaiting_input"
    return check_triage_action(state)


def route_after_validation(state: dict[str, Any]) -> Literal["pass", "retry"]:
    result = check_validation_result(state)
    if result == "pass":
        return "pass"
    retry_count = state.get("retry_count", 0)
    config = state.get("pipeline_config", {})
    max_loops = config.get("pipeline_config", {}).get("max_feedback_loops", 2)
    if retry_count >= max_loops:
        return "pass"
    return "retry"


def route_after_review(state: dict[str, Any]) -> Literal["approved", "feedback"]:
    outputs = state.get("agent_outputs", [])
    reviewer_outputs = [o for o in outputs if o.get("agent_type") == "reviewer"]
    if not reviewer_outputs:
        return "approved"
    latest = reviewer_outputs[-1]
    output_text = (latest.get("output") or "").upper()
    if "APPROVED" in output_text:
        return "approved"
    return "feedback"
