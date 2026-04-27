"""Validation gate node — evaluates agent output for pass/fail routing."""

from typing import Any

import structlog

logger = structlog.get_logger(__name__)

_PASS_SIGNALS = ["all tests passed", "lint clean", "no errors", "passed", "success"]
_FAIL_SIGNALS = ["failed", "error", "failure", "assertion"]


def evaluate_validation(agent_output: dict[str, Any]) -> dict[str, Any]:
    if agent_output.get("is_error"):
        return {"passed": False, "category": "error", "summary": agent_output.get("error_message", "Agent error")}

    output_text = (agent_output.get("output") or "").lower()
    pass_count = sum(1 for s in _PASS_SIGNALS if s in output_text)
    fail_count = sum(1 for s in _FAIL_SIGNALS if s in output_text)

    if pass_count > fail_count:
        return {"passed": True, "category": "full_pass", "summary": agent_output.get("output", "")[:500]}
    return {"passed": False, "category": "full_fail", "summary": agent_output.get("output", "")[:500]}


async def run_validation_node(state: dict[str, Any]) -> dict[str, Any]:
    outputs = state.get("agent_outputs", [])
    validator_outputs = [o for o in outputs if o.get("agent_type") == "validator"]

    if not validator_outputs:
        return {
            "validation_result": {"passed": False, "category": "error", "summary": "No validator output"},
            "current_stage": "validation_complete",
        }

    latest = validator_outputs[-1]
    result = evaluate_validation(latest)
    logger.info("validation_evaluated", category=result["category"], passed=result["passed"])
    return {"validation_result": result, "current_stage": "validation_complete"}
