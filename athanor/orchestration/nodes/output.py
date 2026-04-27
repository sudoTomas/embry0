"""Output assembly node — builds final job result."""

from typing import Any

import structlog

logger = structlog.get_logger(__name__)


def build_output(state: dict[str, Any]) -> dict[str, Any]:
    errors = state.get("errors", [])
    pr_url = state.get("pr_url")
    cost = state.get("total_cost_usd", 0.0)
    outputs = state.get("agent_outputs", [])
    has_errors = any(o.get("is_error") for o in outputs)

    if has_errors or errors:
        stage = "failed"
        summary = f"Job failed with {len(errors)} error(s). Cost: ${cost:.2f}."
        if errors:
            summary += f" Last error: {errors[-1]}"
    elif pr_url:
        stage = "completed"
        summary = f"PR created: {pr_url}. Cost: ${cost:.2f}."
    else:
        stage = "completed"
        summary = f"Job completed. Cost: ${cost:.2f}."

    logger.info("output_assembled", stage=stage, cost_usd=cost, pr_url=pr_url)
    return {"current_stage": stage, "result_summary": summary}


async def run_output_node(state: dict[str, Any]) -> dict[str, Any]:
    return build_output(state)
