"""Issue-to-PR workflow nodes — thin wrappers for the graph."""

from typing import Any

import structlog

logger = structlog.get_logger(__name__)


async def init_node(state: dict[str, Any]) -> dict[str, Any]:
    logger.info("workflow_init", job_id=state.get("job_id"), repo=state.get("repo"))
    return {"current_stage": "initialized", "retry_count": 0}


async def triage_node(state: dict[str, Any]) -> dict[str, Any]:
    """Triage — calls the LLM-backed triage agent to analyze the issue."""
    import os

    from legion.orchestration.nodes.triage import run_triage_node

    api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    auth_token = os.environ.get("CLAUDE_MAX_OAUTH_TOKEN", "")
    provider_mode = os.environ.get("PROVIDER_MODE", "anthropic_api")
    model = os.environ.get("DEFAULT_MODEL", "claude-sonnet-4-6")

    # Determine which credential to use based on provider mode
    if provider_mode == "claude_max" and auth_token:
        return await run_triage_node(state, auth_token=auth_token, model=model)
    elif api_key:
        return await run_triage_node(state, api_key=api_key, model=model)
    else:
        logger.warning("triage_skipped_no_credentials", job_id=state.get("job_id"))
        return {
            "pipeline_config": {
                "action": "proceed",
                "confidence": 0.0,
                "pipeline_template": "standard",
                "pipeline_config": {
                    "sandbox_profile": "default",
                    "agent_models": {"developer": model},
                    "budget_usd": 10.0,
                    "max_feedback_loops": 2,
                    "reviewer_enabled": True,
                    "validator_modes": ["test", "lint", "typecheck"],
                },
                "reasoning": "Skipped triage: no credentials configured.",
            },
            "current_stage": "triage_complete",
        }


async def developer_node(state: dict[str, Any]) -> dict[str, Any]:
    logger.info("developer_node", stage=state.get("current_stage"))
    return {"current_stage": "developer_complete"}


async def validator_node(state: dict[str, Any]) -> dict[str, Any]:
    logger.info("validator_node", stage=state.get("current_stage"))
    return {
        "validation_result": {"passed": True, "category": "full_pass"},
        "current_stage": "validation_complete",
    }


async def reviewer_node(state: dict[str, Any]) -> dict[str, Any]:
    logger.info("reviewer_node", stage=state.get("current_stage"))
    return {"current_stage": "review_complete"}


async def git_ops_node(state: dict[str, Any]) -> dict[str, Any]:
    logger.info("git_ops_node", stage=state.get("current_stage"))
    return {"current_stage": "git_ops_complete"}


async def output_node(state: dict[str, Any]) -> dict[str, Any]:
    from legion.orchestration.nodes.output import build_output
    return build_output(state)


async def retry_developer_node(state: dict[str, Any]) -> dict[str, Any]:
    retry_count = state.get("retry_count", 0)
    logger.info("retry_developer", retry_count=retry_count + 1)
    return {"retry_count": retry_count + 1, "current_stage": "developer_retry"}
